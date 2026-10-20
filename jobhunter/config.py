from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | Path = ROOT / "config.yaml", need_resume: bool = True) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}，请先 cp config.example.yaml config.yaml 并填写")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not need_resume:
        return cfg
    resume_file = cfg.get("resume_file")
    if resume_file and not cfg.get("resume"):
        resume_path = ROOT / resume_file
        if not resume_path.exists():
            raise FileNotFoundError(f"找不到简历文件 {resume_path}，请创建该文件并粘贴简历纯文本")
        cfg["resume"] = resume_path.read_text(encoding="utf-8")
    if not cfg.get("resume"):
        raise ValueError("config.yaml 中需要提供 resume 或 resume_file")
    return cfg
