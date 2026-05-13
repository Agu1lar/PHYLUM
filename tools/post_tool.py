import requests

payload = {"action":"write","path":r"C:\temp\example.txt","content":"hello from post_tool"}
resp = requests.post('http://127.0.0.1:8000/run/tool/filesystem', json=payload)
print('status', resp.status_code)
print(resp.text)
