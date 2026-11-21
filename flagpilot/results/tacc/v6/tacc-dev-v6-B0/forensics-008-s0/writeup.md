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
{"thought": "Let me start by examining the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 527 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.53s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 03:54 .
drwxrwxr-x 4 root root   4096 Nov 21 03:54 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me examine the cat.jpg file. I'll check its file type and look for hidden data using various methods.", "action": "shell", "cmd": "file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
Exit code: 127
Timed out: False
Duration: 111 ms
```

Recorded observation excerpt:

```text
[step 2] shell: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
exit=127  time=0.11s
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
{"thought": "file command not available. Let me use xxd to look at the file header and tail, and also check for steganography.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -5 && echo \"---\" && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -5 && echo "---" && xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1281 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | head -5 && echo "---" && xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.28s
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
{"thought": "I can see the JPEG has EXIF data. I can see 'PicoCTF' in the header area. Let me extract the EXIF data more carefully. I see at offset 0x30 there's 'PicoCTF' text. Let me look at the EXIF segment more carefully.", "action": "shell", "cmd": "xxd -s 0x20 -l 200 ./challenge/cat.jpg"}
```

### Tool: shell

```text
Command: xxd -s 0x20 -l 200 ./challenge/cat.jpg
Exit code: 0
Timed out: False
Duration: 469 ms
```

Recorded observation excerpt:

```text
[step 4] shell: xxd -s 0x20 -l 200 ./challenge/cat.jpg
exit=0  time=0.47s
--- output ---
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f 312e 302f 003c 3f78 7061 636b 6574  p/1.0/.<?xpacket
00000070: 2062 6567 696e 3d27 efbb bf27 2069 643d   begin='...' id=
00000080: 2757 354d 304d 7043 6568 6948 7a72 6553  'W5M0MpCehiHzreS
00000090: 7a4e 5463 7a6b 6339 6427 
```

### Model call 5

Model output (self-reported):

```json
{"thought": "I can see the EXIF data contains XMP metadata. Let me extract the full XMP/EXIF data to find the flag. Let me look for the flag in the EXIF data by searching for 'picoCTF' or 'flag' patterns.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i -E \"pico|flag|ctf\""}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i -E "pico|flag|ctf"
Exit code: 0
Timed out: False
Duration: 488 ms
```

Recorded observation excerpt:

```text
[step 5] shell: strings ./challenge/cat.jpg | grep -i -E "pico|flag|ctf"
exit=0  time=0.49s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>

```

### Model call 6

Model output (self-reported):

```json
{"thought": "The EXIF data only shows 'PicoCTF' as a comment. The flag might be hidden in the image itself (steganography) or in the EXIF data in a more subtle way. Let me look at the full EXIF/XMP data more carefully, and also check for LSB steganography.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | head -50
Exit code: 0
Timed out: False
Duration: 346 ms
```

Recorded observation excerpt:

```text
[step 6] shell: strings ./challenge/cat.jpg | head -50
exit=0  time=0.35s
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

### Model call 7

Model output (self-reported):

```json
{"thought": "I found it! The cc:license field contains a base64-encoded string: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "shell", "cmd": "echo 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9' | base64 -d"}
```

### Tool: shell

```text
Command: echo 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9' | base64 -d
Exit code: 0
Timed out: False
Duration: 177 ms
```

Recorded observation excerpt:

```text
[step 7] shell: echo 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9' | base64 -d
exit=0  time=0.18s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 8

Model output (self-reported):

```json
{"thought": "Found the flag! The base64-encoded string in the EXIF metadata decodes to the flag.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nflag = base64.b64decode('cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9').decode()\nprint(flag)"}
```

### Model call 9

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the flag from it, not hardcode the base64 string. Let me write a script that reads the JPEG file, finds the base64-encoded license URL in the EXIF/XMP data, and decodes it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the cc:license rdf:resource value which contains base64-encoded flag\nmatch = re.search(rb'cc:license rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64 strings that decode to picoCTF\n    for m in re.finditer(rb'[A-Za-z0-9+/]{20,}={0,2}', data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
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

# Find the cc:license rdf:resource value which contains base64-encoded flag
match = re.search(rb'cc:license rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64 strings that decode to picoCTF
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
Duration: 419 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 9
Wall time: 40.66 s
Input tokens: 21707
Output tokens: 974
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
