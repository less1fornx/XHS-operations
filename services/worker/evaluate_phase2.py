from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from packages.agent_core.evaluation import load_cases, run_evaluation
from packages.agent_core.providers import ProviderConfigError, provider_from_env


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 Phase 2 固定案例评测")
    parser.add_argument(
        "--cases",
        type=Path,
        default=PROJECT_ROOT / "evals" / "phase2_cases.v1.json",
    )
    parser.add_argument("--provider", choices=("mock", "qwen"), default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    try:
        provider = provider_from_env(args.provider)
    except ProviderConfigError as exc:
        parser.exit(2, f"配置错误: {exc}\n请复制 .env.example 为 .env，并只在本机填写密钥。\n")
    report = run_evaluation(load_cases(args.cases), provider)
    output = args.output or (
        PROJECT_ROOT
        / "outputs"
        / f"phase2-evaluation-{provider.provider_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **report.summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
