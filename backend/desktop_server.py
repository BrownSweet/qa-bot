"""Packaged local service. The Electron parent owns its lifetime and startup credential."""
import asyncio
import json
import os
import socket
import sys
import threading


def main():
    if os.environ.get("QA_DESKTOP_MODE") != "1":
        raise RuntimeError("请使用桌面应用启动本地服务")
    if "--workspace" in sys.argv:
        from app.workspace import cli
        raise SystemExit(cli(sys.argv[1:]))
    import uvicorn
    from main import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                            timeout_graceful_shutdown=5, loop="asyncio", http="h11")
    server = uvicorn.Server(config)

    # Closing the parent's pipe also stops the backend after a crash of the shell.
    def watch_parent():
        try:
            while sys.stdin.buffer.read(1):
                pass
        finally:
            server.should_exit = True
    threading.Thread(target=watch_parent, daemon=True).start()

    async def serve():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started and not task.done():
            await asyncio.sleep(0.03)
        if server.started:
            print("QA_DESKTOP_READY " + json.dumps({"port": port}), flush=True)
        await task

    try:
        asyncio.run(serve())
    finally:
        sock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("QA_DESKTOP_ERROR " + json.dumps({"message": str(error)}, ensure_ascii=False), flush=True)
        raise
