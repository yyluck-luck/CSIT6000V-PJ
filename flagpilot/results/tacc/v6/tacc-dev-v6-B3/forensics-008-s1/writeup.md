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
{"thought": "Inspect the files first - check the exiftool URL and the cat.jpg file.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && file ./challenge/cat.jpg && ls -la ./challenge/"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && file ./challenge/cat.jpg && ls -la ./challenge/
Exit code: 127
Timed out: False
Duration: 277 ms
```

Recorded observation excerpt:

```text
[step 1] shell: cat ./challenge/exiftool_download_url.txt && file ./challenge/cat.jpg && ls -la ./challenge/
exit=127  time=0.28s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz
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
{"thought": "file command not found. Let me check the image with python and look for hidden data.", "action": "shell", "cmd": "ls -la ./challenge/ && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1041 ms
```

Recorded observation excerpt:

```text
[step 2] shell: ls -la ./challenge/ && xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.04s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 04:07 .
drwxrwxr-x 4 root root   4096 Nov 21 04:07 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt
000d6500: a98e 7f6e 68bf fc6f ff00 297f fd4c 5c2d  ...nh..o..)..L\-
000d6510: 32c0 5d82 ed22 00e3 8a2f ff00 1bff 00ca  2.]..".../......
000d6520: 5fff 0053 1cfe dcd1 7ff8 dffe 52ff 00fa  _..S........R...
000d6530: 9892 6993 c5c8 295c 417f 6de8 bffc 67ff  ..i...)\A.m...g.
```

### Model call 3

Model output (self-reported):

```json
{"thought": "The image is a JPEG. The hint mentions exiftool, so there may be EXIF metadata. Let me check the EXIF data using python.", "action": "python", "code": "import struct\nwith open('./challenge/cat.jpg','rb') as f:\n    data = f.read()\nprint(data[:20])\n# Check for EXIF\nidx = data.find(b'Exif')\nprint('Exif at', idx)\n# Look for any text strings\nimport re\nfor m in re.finditer(rb'[\\x20-\\x7e]{6,}', data):\n    print(m.start(), m.group())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 543 ms
```

Recorded observation excerpt:

```text
[step 3] python
exit=0  time=0.54s
--- output ---
b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00'
Exif at 209
23 b'0Photoshop 3.0'
55 b'PicoCTF'
74 b'http://ns.adobe.com/xap/1.0/'
103 b"<?xpacket begin='"
123 b"' id='W5M0MpCehiHzreSzNTczkc9d'?>"
157 b"<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>"
226 b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
293 b" <rdf:Description rdf:about=''"
324 b"  xmlns:cc='http://creativecommons.org/ns#'>"
369 b"  <cc:license rdf:resource='cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'/>"
445 b' <
```

### Model call 4

Model output (self-reported):

```json
{"thought": "Found a base64 string in the XMP metadata: cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
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
[step 4] decode
exit=0  time=0.00s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 5

Model output (self-reported):

```json
{"thought": "Found the flag. Let me write a standalone script to extract it from the challenge file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import re, base64\nwith open('./challenge/cat.jpg','rb') as f:\n    data = f.read()\n# Find the base64-encoded flag in XMP metadata\nm = re.search(rb'cc:license rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nflag = base64.b64decode(m.group(1)).decode()\nprint(flag)"}
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
with open('./challenge/cat.jpg','rb') as f:
    data = f.read()
# Find the base64-encoded flag in XMP metadata
m = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
flag = base64.b64decode(m.group(1)).decode()
print(flag)
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 548 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 5
Wall time: 22.76 s
Input tokens: 11982
Output tokens: 499
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
