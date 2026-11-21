# FlagPilot Solution Writeup

## Run

```text
Task: forensics-008
Category: Forensics
Model: qwen3.8-27b
Run: forensics-008-s0
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
Duration: 578 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.58s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 20 05:55 .
drwxrwxr-x 4 root root   4096 Nov 20 05:55 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt

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
{"thought": "Let me check the exiftool URL and examine the JPEG file more carefully.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50
Exit code: 0
Timed out: False
Duration: 577 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -50
exit=0  time=0.58s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 3

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP metadata: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 439 ms
```

Recorded observation excerpt:

```text
[step 3] python
exit=0  time=0.44s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 4

Model output (self-reported):

```json
{"thought": "The flag is decoded from the base64 string in the XMP metadata.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 5

Model output (self-reported):

```json
{"thought": "I need to write a script that actually reads the cat.jpg file and extracts the base64 string from the XMP metadata, then decodes it to get the flag.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import re, base64\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section and look for base64-encoded strings\n# The XMP data is in the APP1 segment (0xFFE1)\n# Look for the cc:license rdf:resource attribute which contains base64\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64-looking string in the file\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group(0)).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
```

## Submission

Submission attempts: 2

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import re, base64

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section and look for base64-encoded strings
# The XMP data is in the APP1 segment (0xFFE1)
# Look for the cc:license rdf:resource attribute which contains base64
match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64-looking string in the file
    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):
        try:
            decoded = base64.b64decode(m.group(0)).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except Exception:
            pass
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 711 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 5
Wall time: 30.5 s
Input tokens: 12210
Output tokens: 643
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
