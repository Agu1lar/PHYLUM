# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import requests

payload = {"action":"write","path":r"C:\temp\example.txt","content":"hello from post_tool"}
resp = requests.post('http://127.0.0.1:8000/run/tool/filesystem', json=payload)
print('status', resp.status_code)
print(resp.text)
