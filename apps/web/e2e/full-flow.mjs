// Whole-product walk-through in a real browser, against a fresh database and the mock model.
// Run with scripts/e2e.sh. Each step is something the user does; a failing step names itself.
// When the user finds a problem this did not catch, add the step that would have caught it.
import { chromium } from 'playwright-core'
import { existsSync, mkdirSync, readdirSync, utimesSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const WEB = process.env.E2E_WEB_URL || 'http://127.0.0.1:5192'
const SHOTS = process.env.E2E_SHOT_DIR || ''
const INBOX_DIR = process.env.E2E_INBOX_DIR || ''
const fixture = (name) => join(dirname(fileURLToPath(import.meta.url)), 'fixtures', name)
if (SHOTS) mkdirSync(SHOTS, { recursive: true })

const results = []; const consoleErrors = []; const failedRequests = []
const browser = await chromium.launch({ channel: 'chrome', headless: true })
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
await context.route('**xiaohongshu.com/**', (route) => route.abort())   // never touch the real site
const page = await context.newPage()
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
page.on('pageerror', (e) => consoleErrors.push(String(e)))
page.on('response', (r) => { if (r.status() >= 400 && r.url().includes('/api/')) failedRequests.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`) })

let shot = 0
async function step(name, action) {
  try {
    const detail = await action()
    results.push({ name, ok: true, detail: detail ?? '' })
  } catch (error) {
    results.push({ name, ok: false, detail: String(error).split('\n')[0].slice(0, 160) })
  }
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${String(++shot).padStart(2, '0')}.png`), fullPage: true }).catch(() => {})
}
const expect = (condition, message) => { if (!condition) throw new Error(message) }
const nav = (label) => page.click(`.nav-item:has-text("${label}")`)

// Text cut off by its box. A few places shorten on purpose (list hints, evidence snippets).
const MAY_SHORTEN = ['.run-copy small', '.signal small', '.type-select', 'option', 'select']
async function cutOffText(label) {
  const found = await page.evaluate((allowed) => {
    const out = []
    for (const el of document.querySelectorAll('strong, small, span, h1, h2, p, button, label, em, li')) {
      if (!el.textContent?.trim() || el.children.length > 2 || allowed.some((sel) => el.matches(sel) || el.closest(sel))) continue
      const style = getComputedStyle(el)
      const clips = ['hidden', 'clip'].includes(style.overflowX) || style.textOverflow === 'ellipsis'
      if (clips && el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0) out.push(`${el.tagName.toLowerCase()}.${el.className || '-'}: ${el.textContent.trim().slice(0, 24)}`)
      // one character per line: a label squeezed into a too-narrow column
      if (el.children.length === 0 && el.textContent.trim().length >= 3 && el.clientWidth > 0 && el.clientWidth < 18 && el.clientHeight > 40) out.push(`squeezed ${el.tagName.toLowerCase()}: ${el.textContent.trim().slice(0, 12)}`)
    }
    return [...new Set(out)].slice(0, 6)
  }, MAY_SHORTEN)
  expect(found.length === 0, `${label} 有文字被截断或挤变形：${found.join(' | ')}`)
}

await page.goto(WEB)
await step('打开页面，默认进入候选工作流', async () => { await page.waitForSelector('.tabs'); return await page.locator('h1').textContent() })

await step('进度与反馈：六个环节都显示', async () => {
  await nav('进度与反馈'); await page.waitForSelector('.stage-row')
  expect(await page.locator('.stage-row').count() === 6, '环节数不是 6'); await cutOffText('进度页')
})
await step('提交一条意见，联系方式被隐去', async () => {
  await page.fill('.feedback-form textarea', '检查用意见，电话13800001111'); await page.click('text=提交意见'); await page.waitForSelector('.note-item')
  const text = await page.locator('.note-item p').first().textContent(); expect(!text.includes('13800001111'), '电话没有被隐去'); return text
})

// What pressing the paste key does after a screenshot went to the clipboard.
const pasteImage = (bytes) => page.evaluate((text) => {
  const data = new DataTransfer(); data.items.add(new File([text], 'image.png', { type: 'image/png' }))
  window.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }))
}, bytes)

await step('找店第 1 步：输入搜索词和城市，得到优化后的搜索词，每条是一个去小红书搜索的链接', async () => {
  await nav('批量初筛'); await page.waitForSelector('.finder-suggestion')
  await page.fill('.finder-search input >> nth=1', '深圳'); await page.click('text=给出搜索词')
  await page.waitForSelector('.finder-suggestion:has-text("深圳 西装定制 商务休闲")')
  const links = await page.locator('.finder-suggestion').evaluateAll((items) => items.map((a) => ({ href: a.href, target: a.target, why: a.querySelector('small')?.textContent || '' })))
  expect(links.length >= 6, `搜索词太少：${links.length}`)
  expect(links.every((l) => l.href.startsWith('https://www.xiaohongshu.com/search_result?keyword=') && l.target === '_blank'), '链接应该在新标签页打开小红书搜索')
  expect(links.every((l) => l.why.length > 3), '每条应该写明原因'); await cutOffText('找店')
  return `${links.length} 条`
})
await step('找店第 2 步：粘贴搜索结果页截图，存进“搜索结果”文件夹', async () => {
  await page.click('.finder-mode button:has-text("这是搜索结果页")'); await pasteImage('SEARCH')
  await page.waitForSelector('.finder-ok:has-text("搜索结果")'); expect(readdirSync(join(INBOX_DIR, '搜索结果')).length === 1, '搜索结果截图没有存进去')
})
await step('找店第 2 步：粘贴两张算第 1 家，点“下一家”后再粘贴算第 2 家，名字由系统起', async () => {
  await page.click('.finder-mode button:has-text("这是一家店的截图")')
  await pasteImage('PRICE:2999'); await page.waitForSelector('.finder-ok:has-text("第1家-1.png")')
  await pasteImage('x'); await page.waitForSelector('.finder-ok:has-text("第1家-2.png")')
  expect((await page.locator('.finder-current span').textContent()).includes('已有 2 张'), '粘贴后“已有几张”没有立刻更新')
  await page.click('.finder-current button:has-text("下一家")'); await pasteImage('x'); await page.waitForSelector('.finder-ok:has-text("第2家-1.png")')
  const names = readdirSync(INBOX_DIR).filter((n) => n.endsWith('.png')).sort()
  expect(names.length === 3 && names.every((n) => /^找店\d{4}-\d{4}-第[12]家-[12]\.png$/.test(n)), `文件名不符合规则：${names.join(',')}`)
  return names.join('、')
})
await step('粘贴的不是图片时有提示', async () => {
  await page.evaluate(() => { const d = new DataTransfer(); d.setData('text/plain', 'hello'); window.dispatchEvent(new ClipboardEvent('paste', { clipboardData: d, bubbles: true })) })
  await page.waitForSelector('.unclear:has-text("剪贴板里没有图片")')
})
await step('找店第 3 步：读取，第 1 家用了 2 张图，共两张卡片', async () => {
  await page.waitForSelector('.finder-pending li:has-text("第1家：2 张")', { timeout: 15000 }); await page.waitForSelector('.finder-panel .badge:has-text("3 张待读取")', { timeout: 15000 })
  await page.click('.finder-panel .primary-button'); await page.waitForFunction(() => document.querySelectorAll('.shop-card').length === 2, null, { timeout: 60000 })
  const first = await page.locator('.shop-card >> nth=0').evaluate((el) => ({ badge: el.querySelector('header .badge')?.textContent, images: el.querySelectorAll('.thumbs a').length }))
  expect(first.badge === '优先看' && first.images === 2, `第 1 家应排最前且有 2 张图：${JSON.stringify(first)}`)
  expect(readdirSync(INBOX_DIR).filter((n) => n.endsWith('.png')).length === 0, '读完后原图应该已归档'); await page.click('text=返回上传')
})

const INBOX = process.env.E2E_INBOX_DIR || ''
const drop = (name, content) => {      // a screenshot saved a moment ago by someone browsing
  const file = join(INBOX, name); mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, content)
  const past = new Date(Date.now() - 60_000); utimesSync(file, past, past)
}
await step('截图文件夹：不上传、不改名，系统自己认出哪几张是同一家', async () => {
  expect(INBOX, '没有配置截图文件夹')
  drop('截屏 10.01.png', 'SHOP:甲店'); drop('截屏 10.02.png', 'SHOP:甲 店'); drop('乙店-1.png', 'PRICE:2999'); drop('丙店/随便.png', 'x'); drop('截屏 10.05.png', 'SEARCH'); drop('截屏 10.06.png', 'DM')
  await nav('批量初筛'); await page.waitForSelector('.finder-panel .badge:has-text("6 张待读取")', { timeout: 15000 })
  await page.click('.finder-panel .primary-button'); await page.waitForFunction(() => document.querySelectorAll('.shop-card').length === 3, null, { timeout: 60000 })
  const notes = await page.locator('.inbox-notes').innerText()
  expect(notes.includes('搜索结果页') && notes.includes('私信截图不在这里读取'), `没有说明被跳过的截图：${notes}`)
  expect(readdirSync(INBOX).sort().join(',') === '已读取,搜索结果,没认出来', `文件夹里应该只剩三个结果文件夹：${readdirSync(INBOX).join(',')}`)
  const done = readdirSync(join(INBOX, '已读取')).flatMap((stamp) => readdirSync(join(INBOX, '已读取', stamp)))
  expect(['截屏 10.01.png', '截屏 10.02.png', '乙店-1.png', '丙店-随便.png'].every((name) => done.includes(name)) && existsSync(join(INBOX, '搜索结果', '截屏 10.05.png')), `原图没有按结果归档：${done.join(',')}`)
  await cutOffText('文件夹读取结果'); await page.click('text=返回上传')
  await page.waitForSelector('.finder-panel .badge:has-text("0 张待读取")'); expect(await page.locator('.finder-panel .primary-button').isDisabled(), '没有新截图时按钮应该不可点')
})

await step('排先后：标价够高的排最前，没有价格的其次并提示补图，最贵也不到门槛的不联系', async () => {
  await nav('批量初筛'); const inputs = page.locator('.panel input[type=file]')
  await inputs.nth(0).setInputFiles(fixture('shop-cheap.png')); await inputs.nth(1).setInputFiles(fixture('shop-a.png')); await inputs.nth(2).setInputFiles(fixture('shop-priced.png'))
  await page.click('text=开始初筛'); await page.waitForFunction(() => document.querySelectorAll('.shop-card').length === 3)
  const cards = await page.locator('.shop-card').evaluateAll((items) => items.map((el) => ({ badge: el.querySelector('header .badge')?.textContent, text: el.textContent })))
  expect(cards.map((c) => c.badge).join(',') === '优先看,其次,不联系', `顺序不对：${cards.map((c) => c.badge).join(',')}`)
  expect(cards[0].text.includes('价格看得见且够高（最高 2999 元）'), '第一家没有写明价格依据')
  expect(cards[1].text.includes('建议补一张带价格的截图'), '没有价格的店没有提示补图')
  expect(cards[2].text.includes('看得到的最高价格 980 元，低于 1600 元'), '不联系的店没有写明原因')
  expect(await page.locator('.shop-card >> nth=2 >> footer').count() === 0, '不联系的店不应该有路线选项')
  return cards.map((c) => c.badge).join(' → ')
})
await step('卡片上有“只记录、暂不影响先后的事实”，文字没有被截断', async () => {
  await page.locator('.shop-card >> nth=0 >> .recorded summary').click()
  const facts = await page.locator('.shop-card >> nth=0 >> .recorded dt').allTextContents()
  expect(['置顶笔记', '账号', '门店信息'].every((name) => facts.includes(name)), `缺少记录项：${facts.join(',')}`); await cutOffText('排先后结果')
  await page.click('text=返回上传')
})

await step('批量初筛：两家店，一家读不出来', async () => {
  const inputs = page.locator('.panel input[type=file]')
  await inputs.nth(0).setInputFiles(fixture('shop-a.png')); await inputs.nth(1).setInputFiles(fixture('unreadable.png'))
  await page.click('text=开始初筛'); await page.waitForSelector('.failure-box')
  expect(await page.locator('.shop-card').count() === 1 && await page.locator('.failure-row').count() === 1, '应为读成 1 家、失败 1 家')
})
await step('失败的店在原批次里换图重试', async () => {
  await page.locator('.failure-row input[type=file]').setInputFiles(fixture('shop-b.png'))
  await page.waitForFunction(() => document.querySelectorAll('.shop-card').length === 2 && !document.querySelector('.failure-box'))
})
await step('初筛结果：填小红书号、标注分档，文字没有被截断', async () => {
  const id = page.locator('.id-row input').first(); await id.fill('e2e_shop_01'); await id.blur(); await page.waitForTimeout(500)
  await page.click('.shop-card >> nth=0 >> button:has-text("同意")'); await cutOffText('初筛结果')
})
await step('确认名单并生成草稿', async () => {
  await page.click('text=确认名单并生成草稿'); await page.waitForSelector('text=草稿已生成', { timeout: 60000 })
})
await step('不刷新网页，从导航进入工作流就能看到新任务', async () => {
  await nav('候选工作流'); await page.click('.tabs button:has-text("待处理")')
  await page.waitForSelector('.run-row', { timeout: 5000 }); const count = await page.locator('.run-row').count()
  expect(count >= 1, '待处理里没有新任务'); return `${count} 条`
})
await step('历史批次标题完整显示', async () => {
  await nav('批量初筛'); if (await page.locator('text=返回上传').count()) await page.click('text=返回上传')
  await page.waitForSelector('.batch-row'); const title = await page.locator('.batch-row .run-copy strong').first().textContent()
  expect(/\d{4}年/.test(title) && title.includes('家店'), `标题不完整：${title}`); await cutOffText('历史批次'); return title
})

await step('审文案：写一句话让它重写', async () => {
  await nav('候选工作流'); await page.click('.tabs button:has-text("待处理")')
  await page.locator('.run-row', { hasText: '私信' }).first().click(); await page.waitForSelector('.draft-editor')
  await page.fill('.rewrite-row input', '再短一点'); await page.click('.rewrite-row button')
  await page.waitForFunction(() => document.querySelector('.draft-editor')?.value.includes('已按'), null, { timeout: 15000 })
})
await step('审文案：带违规内容的重写被拦下，原草稿保留', async () => {
  const before = await page.inputValue('.draft-editor'); await page.fill('.rewrite-row input', '加上微信联系'); await page.click('.rewrite-row button')
  await page.waitForSelector('.alert.error'); expect(await page.inputValue('.draft-editor') === before, '原草稿被改掉了'); await page.click('.alert.error .alert-close')
  failedRequests.length = 0; consoleErrors.length = 0   // the 409 above is the expected refusal
})
await step('改草稿并选原因，通过并现在去发，文案被复制', async () => {
  await page.fill('.draft-editor', (await page.inputValue('.draft-editor')) + ' 改'); await page.waitForSelector('.edit-reasons'); await page.click('.edit-reasons .tag >> nth=0')
  await page.click('text=通过并现在去发'); await page.waitForSelector('text=发送成功了吗'); expect(await page.locator('.final-text p').count() === 1, '发送中看不到文案')
})
await step('确认已发送，进入等回复', async () => { await page.click('text=已发送成功'); await page.waitForSelector('.reply-panel') })
await step('记录回复：不选阶段不能保存；选“不需要”自动带出阶段', async () => {
  await page.fill('.reply-panel textarea', '不用了谢谢'); expect(await page.locator('button:has-text("保存回复")').isDisabled(), '没选阶段却能保存')
  await page.click('.reply-panel .tag:has-text("不需要")'); expect(await page.locator('.reply-panel select').inputValue() === 'replied_no_intent', '阶段没有自动带出')
  await page.click('button:has-text("保存回复")'); await page.waitForSelector('.badge:has-text("已回复")')
})

await step('手动添加一家店，模型给出建议', async () => {
  await page.click('.tabs button:has-text("待处理")'); await page.click('text=手动添加')
  await page.fill('input[placeholder^="例如：上海"]', '手动添加的店'); await page.fill('textarea[placeholder^="粘贴对方"]', '法兰绒双排扣西装上新')
  await page.click('text=开始评选'); await page.waitForSelector('.choice-group')
})
await step('不联系必须先说原因', async () => {
  await page.click('.choice.danger'); await page.waitForSelector('.end-panel')
  expect(await page.locator('text=确认不联系，结束任务').isDisabled(), '没选原因却能结束')
  await page.click('.end-panel .tag >> nth=0'); await page.click('text=确认不联系，结束任务'); await page.waitForSelector('text=任务已结束')
})
await step('原因汇总里出现刚才的原因', async () => {
  await nav('进度与反馈'); await page.waitForSelector('.reason-row')
  const text = (await page.locator('.reason-columns').innerText()).replace(/\s+/g, ' ')
  expect(text.includes('不是西装定制店'), '选路线时填的不联系原因没有进汇总'); expect(text.includes('太长'), '审文案时选的修改原因没有进汇总'); return text.slice(0, 80)
})
await step('风险记录页能打开', async () => { await nav('风险记录'); await page.waitForSelector('.risk-list') })

await step('手机宽度：导航有文字，各页文字没有被截断或挤成竖排', async () => {
  await page.setViewportSize({ width: 390, height: 800 })
  const labels = await page.locator('.nav-item').evaluateAll((items) => items.map((el) => getComputedStyle(el).fontSize))
  expect(labels.every((size) => parseFloat(size) >= 10), '手机上导航没有文字')
  for (const label of ['进度与反馈', '批量初筛', '候选工作流']) { await nav(label); await page.waitForTimeout(300); await cutOffText(`手机·${label}`) }
  await page.click('.tabs button:has-text("全部")'); await page.locator('.run-row').first().click(); await page.waitForTimeout(300); await cutOffText('手机·任务详情')
})

await step('全程没有页面报错', async () => { expect(consoleErrors.length === 0, consoleErrors.join(' | ')) })
await step('全程没有意外失败的接口请求', async () => { expect(failedRequests.length === 0, failedRequests.join(' | ')) })
await browser.close()

const failed = results.filter((r) => !r.ok)
for (const r of results) console.log(`${r.ok ? '通过' : '失败'}  ${r.name}${r.detail ? `  →  ${r.detail}` : ''}`)
console.log(`\n共 ${results.length} 步，通过 ${results.length - failed.length}，失败 ${failed.length}${SHOTS ? `。截图在 ${SHOTS}` : ''}`)
process.exit(failed.length ? 1 : 0)
