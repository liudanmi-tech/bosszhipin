"""启动本地网页：python -m jobhunter.web  →  http://127.0.0.1:8765"""
import threading
import webbrowser

import uvicorn

PORT = 8765


def main():
    url = f"http://127.0.0.1:{PORT}"
    print(f"AI 求职助手已启动：{url}  （关闭此窗口即退出）")
    threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run("jobhunter.web.server:app", host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
