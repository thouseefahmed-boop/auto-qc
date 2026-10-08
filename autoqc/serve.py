"""Uvicorn launcher with application-scoped TCP MSS for the internal VPN.

Small packets avoid a PMTU black hole observed on the H200 -> Mac VPN path.
Only this application's listener changes; no host networking is modified.
"""
import os
import socket

import uvicorn
from dotenv import load_dotenv


def main():
    load_dotenv()
    port = int(os.getenv("AUTOQC_PORT", "8810"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "TCP_MAXSEG"):
            listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG,
                                int(os.getenv("AUTOQC_TCP_MSS", "1200")))
        listener.bind((os.getenv("AUTOQC_HOST", "0.0.0.0"), port))
        listener.set_inheritable(True)
        uvicorn.run("autoqc.api:app", fd=listener.fileno(), workers=2,
                    access_log=False, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
