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
Duration: 566 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.57s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 20 06:00 .
drwxrwxr-x 4 root root   4096 Nov 20 06:00 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me examine the cat.jpg file - check its EXIF data and look for hidden content. First let me check if exiftool is available, and also look at the file with strings and hexdump.", "action": "shell", "cmd": "file ./challenge/cat.jpg && strings ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: file ./challenge/cat.jpg && strings ./challenge/cat.jpg | head -50
Exit code: 127
Timed out: False
Duration: 153 ms
```

Recorded observation excerpt:

```text
[step 2] shell: file ./challenge/cat.jpg && strings ./challenge/cat.jpg | head -50
exit=127  time=0.15s
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
{"thought": "file command not available. Let me try strings and hexdump instead.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | head -80"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | head -80
Exit code: 0
Timed out: False
Duration: 552 ms
```

Recorded observation excerpt:

```text
[step 3] shell: strings ./challenge/cat.jpg | head -80
exit=0  time=0.55s
--- output ---
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
 </rdf:Description>
 <rdf:Description rdf:about=''
  xmlns:dc='http://purl.org/dc/elements/1.1/'>
  <dc:rights
```

### Model call 4

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP metadata: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 787 ms
```

Recorded observation excerpt:

```text
[step 4] python
exit=0  time=0.79s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 5

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64 string in the XMP metadata of the JPEG file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 6

Model output (self-reported):

```json
{"thought": "I need to write a script that actually reads the cat.jpg file and extracts the base64 string from the XMP metadata, then decodes it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import re, base64\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded license URL in XMP metadata\nmatch = re.search(rb'cc:license rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64 string that decodes to picoCTF\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
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

# Find the base64-encoded license URL in XMP metadata
match = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64 string that decodes to picoCTF
    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):
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
Duration: 426 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 6
Wall time: 30.71 s
Input tokens: 6100
Output tokens: 666
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
