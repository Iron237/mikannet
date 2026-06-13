"""首启:用临时密码把 qB WebUI 密码固定为 .env 里的值。用法: set_qb_password.py <temp_pw> <new_pw>"""
import sys

import qbittorrentapi

temp_pw, new_pw = sys.argv[1], sys.argv[2]
qb = qbittorrentapi.Client(host="localhost", port=18080, username="admin", password=temp_pw)
qb.auth_log_in()
qb.app.set_preferences({"web_ui_username": "admin", "web_ui_password": new_pw})
print("qB 密码已固定: admin /", new_pw)
