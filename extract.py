import json, base64, os
with open('setup2.py', 'r', encoding='utf-8') as f:
    bundle = json.load(f)
for path, encoded_content in bundle.items():
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, 'wb') as f:
        f.write(base64.b64decode(encoded_content))
    print(f"Extracted: {path}")