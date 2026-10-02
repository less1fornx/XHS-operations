# Node.js 运行时

项目统一使用 Node.js `24.21.0`，对应 npm `11.19.0`。

选择原因：Node 24 在项目创建时处于 Active LTS，支持周期长，适合 React/Vite 前端；Node 26 仍属于 Current，不作为项目运行时；Node 22 已进入 Maintenance LTS，不作为新项目的默认版本。

项目通过 `.node-version` 固定版本。当前机器的 Node 二进制安装在用户级目录：

```text
/Users/askvenht/.local/node/node-v24.21.0-darwin-arm64
```

在本项目中使用：

```bash
export PATH=/Users/askvenht/.local/node/node-v24.21.0-darwin-arm64/bin:$PATH
node --version
npm --version
```

预期输出：

```text
v24.21.0
11.19.0
```

不要在项目中混用 Node 22、Node 26 或系统全局 npm。依赖版本由前端目录中的 `package-lock.json` 锁定。
