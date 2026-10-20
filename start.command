#!/bin/bash
# 双击运行：首次会自动安装依赖（几分钟），之后秒开。网页地址 http://127.0.0.1:8765
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 python3，请先安装：https://www.python.org/downloads/"
  read -r -p "按回车退出"; exit 1
fi
if [ ! -d .venv ]; then
  echo "首次运行，正在创建环境..."
  python3 -m venv .venv || { read -r -p "创建环境失败，按回车退出"; exit 1; }
fi
source .venv/bin/activate

echo "检查依赖..."
pip install -q -r requirements.txt || { read -r -p "安装依赖失败，按回车退出"; exit 1; }
python -m playwright install chromium >/dev/null 2>&1 || python -m playwright install chromium

python -m jobhunter.web
