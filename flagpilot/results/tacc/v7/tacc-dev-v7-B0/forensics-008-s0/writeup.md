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
{"thought": "Let me start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 952 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.95s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 19:09 .
drwxrwxr-x 4 root root   4096 Nov 21 19:09 ..
-r--r--r-- 1 root root 878136 Nov 21 17:59 cat.jpg
-r--r--r-- 1 root root     48 Nov 21 17:59 exiftool_download_url.txt
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me examine the cat.jpg file. First, let me check its file type and look for any hidden data.", "action": "shell", "cmd": "file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
Exit code: 127
Timed out: False
Duration: 440 ms
```

Recorded observation excerpt:

```text
[step 2] shell: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
exit=127  time=0.44s
--- output ---
[stderr]
/bin/sh: 1: file: not found

```

Recorded stderr excerpt:

```text
/bin/sh: 1: file: not found

```

### Model call 3

Model output (self-reported):

```json
{"thought": "file command not available. Let me use xxd to look at the file header and tail.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -5 && echo '---' && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -5 && echo '---' && xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 2778 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | head -5 && echo '---' && xxd ./challenge/cat.jpg | tail -20
exit=0  time=2.78s
--- output ---
00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
---
000d6500: a98e 7f6e 68bf fc6f ff00 297f fd4c 5c2d  ...nh..o..)..L\-
000d6510: 32c0 5d82 ed22 00e3 8a2f ff00 1bff 00ca  2
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 4

Model output (self-reported):

```json
{"thought": "I can see the JPEG has EXIF data. Let me look at the EXIF section more carefully. I see 'PicoCTF' in the header area. Let me extract the EXIF data to find the flag.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -40"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -40
Exit code: 0
Timed out: False
Duration: 528 ms
```

Recorded observation excerpt:

```text
[step 4] shell: xxd ./challenge/cat.jpg | head -40
exit=0  time=0.53s
--- output ---
00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f 312e 302f 003c 3f78 7061 636b 6574  p/1.0/.<?xpacket
00000070: 2062 6567 696e 3d27 efbb bf27
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 5

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP metadata: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 475 ms
```

Recorded observation excerpt:

```text
[step 5] python
exit=0  time=0.47s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 6

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64 string in the XMP metadata of the JPEG file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 7

Model output (self-reported):

```json
{"thought": "The script needs to actually read from the challenge file and extract the base64 string from the XMP metadata, not hardcode it. Let me write a script that parses the JPEG file to find the base64-encoded flag in the XMP metadata.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section (starts with ffe1 marker)\n# Look for base64-encoded string in the XMP data\n# The XMP section contains a cc:license rdf:resource with base64\n# Find the base64 string between rdf:resource=' and '/>\nmatch = re.search(rb\"rdf:resource='([A-Za-z0-9+/=]+)'/>\", data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64 string that decodes to picoCTF\n    for m in re.finditer(rb\"[A-Za-z0-9+/]{20,}={0,2}\", data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
```

## Submission

Submission attempts: 2

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import base64
import re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP metadata section (starts with ffe1 marker)
# Look for base64-encoded string in the XMP data
# The XMP section contains a cc:license rdf:resource with base64
# Find the base64 string between rdf:resource=' and '/>
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64 string that decodes to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/]{20,}={0,2}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
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
Duration: 505 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 7
Wall time: 41.2 s
Input tokens: 18122
Output tokens: 813
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
