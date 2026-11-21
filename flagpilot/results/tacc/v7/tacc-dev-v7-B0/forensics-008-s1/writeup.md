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
{"thought": "Let me start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 679 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.68s
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
{"thought": "Let me check if exiftool is available, and also look at the file with strings and hexdump to see if there's anything hidden.", "action": "shell", "cmd": "which exiftool 2>/dev/null; file ./challenge/cat.jpg; strings ./challenge/cat.jpg | grep -i 'picoCTF\\|flag' | head -20"}
```

### Tool: shell

```text
Command: which exiftool 2>/dev/null; file ./challenge/cat.jpg; strings ./challenge/cat.jpg | grep -i 'picoCTF\|flag' | head -20
Exit code: 0
Timed out: False
Duration: 709 ms
```

Recorded observation excerpt:

```text
[step 2] shell: which exiftool 2>/dev/null; file ./challenge/cat.jpg; strings ./challenge/cat.jpg | grep -i 'picoCTF\|flag' | head -20
exit=0  time=0.71s
--- output ---
/mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>

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
{"thought": "Exiftool is available. Let me use it to extract all metadata from the cat.jpg file.", "action": "shell", "cmd": "exiftool -a -G1 ./challenge/cat.jpg 2>&1 | head -100"}
```

### Tool: shell

```text
Command: exiftool -a -G1 ./challenge/cat.jpg 2>&1 | head -100
Exit code: 0
Timed out: False
Duration: 650 ms
```

Recorded observation excerpt:

```text
[step 3] shell: exiftool -a -G1 ./challenge/cat.jpg 2>&1 | head -100
exit=0  time=0.65s
--- output ---
ExifTool Version Number         : FlagPilot shim 1.0 (subset: EXIF/XMP/PNG/GIF/PDF text tags)
File Name                       : cat.jpg
Directory                       : ./challenge
File Size                       : 878 kB
File Type                       : JPEG
File Type Extension             : jpg
MIME Type                       : image/jpeg
JFIF Version                    : 1.02
Rights                          : PicoCTF
Image Width                     : 2560
Image Height                    
```

### Model call 4

Model output (self-reported):

```json
{"thought": "The exiftool shim is limited. Let me look at the raw EXIF data more carefully. The challenge says files can be changed in a secret way - maybe there's hidden data in the EXIF or appended to the file. Let me check the hex dump of the file, especially the end, and look for any appended data after the JPEG EOI marker.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -40"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -40
Exit code: 0
Timed out: False
Duration: 978 ms
```

Recorded observation excerpt:

```text
[step 4] shell: xxd ./challenge/cat.jpg | tail -40
exit=0  time=0.98s
--- output ---
000d63c0: e714 effb c7a0 7ff6 43ff 0092 e7ff 0053  ........C......S
000d63d0: 03fe f1a8 3ffd 90ff 00e4 b9ff 00d4 c493  ....?...........
000d63e0: 4997 02fa bac1 4be7 ac54 7fef 1a81 ff00  I.....K..T......
000d63f0: c7ff 00f2 5cff 00ea 607f de2d 03ff 008f  ....\...`..-....
000d6400: ff00 e4b9 ff00 d4c4 9349 96cd b98e 178f  .........I......
000d6410: 58a9 ff00 de35 07ff 008f ff00 e4b9 ff00  X....5..........
000d6420: d4c1 7fef 1285 ff00 c7ff 00f2 5cff 00ea  ............\...
000d6430: 6249 a4cb 5978 f58e 178c 550f
```

### Model call 5

Model output (self-reported):

```json
{"thought": "The file ends with ffd9 which is the JPEG EOI marker, so no data appended after. Let me look at the EXIF data more carefully. The challenge says files can be changed in a secret way - maybe there's a hidden comment or modified EXIF field. Let me look at the raw EXIF bytes.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -80"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -80
Exit code: 0
Timed out: False
Duration: 523 ms
```

Recorded observation excerpt:

```text
[step 5] shell: xxd ./challenge/cat.jpg | head -80
exit=0  time=0.52s
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

### Model call 6

Model output (self-reported):

```json
{"thought": "I can see in the XMP data there's a base64-encoded string in the cc:license rdf:resource field: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 524 ms
```

Recorded observation excerpt:

```text
[step 6] python
exit=0  time=0.52s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 7

Model output (self-reported):

```json
{"thought": "The base64 string in the XMP metadata decodes to the flag: picoCTF{the_m3tadata_1s_modified}", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 8

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the base64 string from the XMP metadata. Let me write a script that reads cat.jpg, finds the base64 string in the XMP data, and decodes it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP section and look for base64-encoded string in cc:license rdf:resource\n# The pattern is: rdf:resource='BASE64STRING'/>\nmatch = re.search(rb\"rdf:resource='([A-Za-z0-9+/=]+)'/>\", data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64-looking string that decodes to picoCTF\n    for m in re.finditer(rb\"[A-Za-z0-9+/]{20,}={0,2}\", data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
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

# Find the XMP section and look for base64-encoded string in cc:license rdf:resource
# The pattern is: rdf:resource='BASE64STRING'/>
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for any base64-looking string that decodes to picoCTF
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
Duration: 731 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 8
Wall time: 43.52 s
Input tokens: 29468
Output tokens: 949
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
