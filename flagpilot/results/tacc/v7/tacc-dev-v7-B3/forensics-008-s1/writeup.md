# FlagPilot Solution Writeup

## Run

```text
Task: forensics-008
Category: Forensics
Model: qwen3.8-27b
Run: forensics-008-s1
Verdict: verified
Reason: match
```

The verdict below is the recorded outcome. Model reasoning is a self-report; tool and replay outputs are recorded execution evidence.

## Challenge

```text
Files can always be changed in a secret way. Can you find the flag?

Files provided (in ./challenge/):
  - cat.jpg
  - exiftool_download_url.txt
```

## Recorded Steps

### Model call 1

Model output (self-reported):

```json
{"thought": "Let me start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
Exit code: 127
Timed out: False
Duration: 628 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.63s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 19:17 .
drwxrwxr-x 4 root root   4096 Nov 21 19:17 ..
-r--r--r-- 1 root root 878136 Nov 21 17:59 cat.jpg
-r--r--r-- 1 root root     48 Nov 21 17:59 exiftool_download_url.txt

[stderr]
/bin/sh: 1: file: not found

```

Recorded stderr excerpt:

```text
/bin/sh: 1: file: not found

```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me check the exiftool URL file and look at the image with strings and hexdump.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && echo \"---\" && strings ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && echo "---" && strings ./challenge/cat.jpg | head -50
Exit code: 0
Timed out: False
Duration: 782 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && echo "---" && strings ./challenge/cat.jpg | head -50
exit=0  time=0.78s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz---
JFIF
0Photoshop 3.0
8BIM
PicoCTF
http://ns.adobe.com/xap/1.0/
<?xpacket begin='
' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>
 <rdf:Description rdf:about=''
  xmlns:cc='http://creativecommons.org/ns#'>
  <cc:license rdf:resource='cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'/>
```

### Model call 3

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP metadata: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
```

### Tool: decode

```text
Command: 
Exit code: 0
Timed out: False
Duration: 0 ms
```

Recorded observation excerpt:

```text
[step 3] decode
exit=0  time=0.00s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 4

Model output (self-reported):

```json
{"thought": "I found the flag. Let me create a standalone script that extracts it from the challenge file.", "action": "python", "code": "import re, base64\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded flag in the XMP metadata\n# The flag is in the cc:license rdf:resource attribute\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64 strings that decode to picoCTF\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if decoded.startswith('picoCTF'):\n                print(decoded)\n                break\n        except:\n            pass"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 738 ms
```

Recorded observation excerpt:

```text
[step 4] python
exit=0  time=0.74s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 5

Model output (self-reported):

```json
{"thought": "The script works. Let me test it with run_script to make sure it works in a clean directory.", "action": "run_script", "script": "import re, base64\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded flag in the XMP metadata\n# The flag is in the cc:license rdf:resource attribute\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64 strings that decode to picoCTF\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if decoded.startswith('picoCTF'):\n                print(decoded)\n                break\n        except:\n            pass"}
```

### Tool: run_script

```text
Command: 
Exit code: 0
Timed out: False
Duration: 655 ms
```

Recorded observation excerpt:

```text
[step 5] run_script
exit=0  time=0.66s
--- output ---
picoCTF{the_m3tadata_1s_modified}
hint: 脚本在干净目录里跑通了，打印出 1 个 flag 形态。如果这就是你要提交的答案，可以 submit 了。
```

### Model call 6

Model output (self-reported):

```json
{"thought": "The script works correctly in a clean directory and prints the flag. Let me submit it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import re, base64\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded flag in the XMP metadata\n# The flag is in the cc:license rdf:resource attribute\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64 strings that decode to picoCTF\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if decoded.startswith('picoCTF'):\n                print(decoded)\n                break\n        except:\n            pass"}
```

## Submission

Submission attempts: 1

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import re, base64

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the base64-encoded flag in the XMP metadata
# The flag is in the cc:license rdf:resource attribute
match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64 strings that decode to picoCTF
    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if decoded.startswith('picoCTF'):
                print(decoded)
                break
        except:
            pass
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 578 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 6
Wall time: 36.75 s
Input tokens: 9275
Output tokens: 1007
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
