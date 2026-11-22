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
{"thought": "Start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && file ./challenge/cat.jpg"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && file ./challenge/cat.jpg
Exit code: 127
Timed out: False
Duration: 907 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg
exit=127  time=0.91s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 22 01:05 .
drwxrwxr-x 4 root root   4096 Nov 22 01:05 ..
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
{"thought": "Let me check the exiftool download URL and look at the image file header.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -20"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -20
Exit code: 0
Timed out: False
Duration: 1786 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && xxd ./challenge/cat.jpg | head -20
exit=0  time=1.79s
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
{"thought": "I can see in the hex dump at offset 0x30-0x40 there's 'PicoCTF' in the Photoshop metadata. Let me extract more of the file to find the complete flag.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -50"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -50
Exit code: 0
Timed out: False
Duration: 775 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | head -50
exit=0  time=0.78s
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

### Model call 4

Model output (self-reported):

```json
{"thought": "I can see 'PicoCTF' in the metadata but I need to find the complete flag. Let me search for the flag pattern in the file.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i 'pico'"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i 'pico'
Exit code: 0
Timed out: False
Duration: 411 ms
```

Recorded observation excerpt:

```text
[step 4] shell: strings ./challenge/cat.jpg | grep -i 'pico'
exit=0  time=0.41s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>

```

### Model call 5

Model output (self-reported):

```json
{"thought": "The flag seems to be just 'PicoCTF' but that doesn't match the format picoCTF{...}. Let me look more carefully at the hex dump around the PicoCTF area. I see at offset 0x30-0x40: '1c02 7400 0750 6963 6f43 5446 1c02 0000 0200 0400'. The '07' before 'PicoCTF' might indicate a length of 7, but PicoCTF is 7 chars. Wait, let me look at the full context more carefully. The flag format is picoCTF{...} so maybe there's more hidden data. Let me check if there's steganography or look at the full file more carefully.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | grep -i 'pico\\|flag\\|{' | head -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | grep -i 'pico\|flag\|{' | head -20
Exit code: 0
Timed out: False
Duration: 489 ms
```

Recorded observation excerpt:

```text
[step 5] shell: xxd ./challenge/cat.jpg | grep -i 'pico\|flag\|{' | head -20
exit=0  time=0.49s
--- output ---
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000250: 2778 2d64 6566 6175 6c74 273e 5069 636f  'x-default'>Pico
00000fd0: 942b 7be9 0408 2544 1e37 8552 d942 2e4f  .+{...%D.7.R.B.O
000010a0: 7ba9 5905 4624 90b0 2c61 5420 2b81 0601  {.Y.F$..,aT +...
000010d0: a8f4 836c 7288 9242 c1e0 8d7b fd7f 4836  ...lr..B...{..H6
000012a0: e11f 227b 60c1 b314 6c4b 5375 c61c 6b3c  .."{`...lKSu..k<
00001300: 4cf9 118c 45f1 2553 ff00 b71f ce21 e27b  L...E.%S.....!.{
00001510: 465
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 6

Model output (self-reported):

```json
{"thought": "The flag seems to be just 'PicoCTF' in the metadata, but the format should be picoCTF{...}. Let me look more carefully at the hex around the PicoCTF string. At offset 0x30: '0013 1c02 7400 0750 6963 6f43 5446 1c02 0000 0200 0400'. The '07' before PicoCTF could be a length byte. But wait - maybe the flag is hidden in the image itself (steganography). Let me check if there's data appended to the end of the JPEG file, or look for hidden data in the image pixels.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1137 ms
```

Recorded observation excerpt:

```text
[step 6] shell: xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.14s
--- output ---
000d6500: a98e 7f6e 68bf fc6f ff00 297f fd4c 5c2d  ...nh..o..)..L\-
000d6510: 32c0 5d82 ed22 00e3 8a2f ff00 1bff 00ca  2.]..".../......
000d6520: 5fff 0053 1cfe dcd1 7ff8 dffe 52ff 00fa  _..S........R...
000d6530: 9892 6993 c5c8 295c 417f 6de8 bffc 67ff  ..i...)\A.m...g.
000d6540: 0029 7ffd 4c73 fb6f 45ff 00e3 7ff9 4bfe  .)..Ls.oE.....K.
000d6550: 9124 de4e 15c1 73c4 21c6 d453 ff00 be7f  .$.N..s.!..S....
000d6560: f297 ff00 d4c7 3fb6 d46f fe37 ff00 94bf  ......?..o.7....
000d6570: e912 4de4 ee7f 2819 c748 81fe
```

### Model call 7

Model output (self-reported):

```json
{"thought": "The file ends with ffd9 which is the normal JPEG EOI marker. Let me look more carefully at the metadata. The hex at 0x30 shows: '0013 1c02 7400 0750 6963 6f43 5446 1c02 0000 0200 0400'. Let me decode this more carefully. The '07' before 'PicoCTF' might be a length indicator. But the flag format is picoCTF{...}. Let me look at the full metadata section more carefully, maybe there's more data I'm missing. Let me extract the full XMP metadata.", "action": "python", "code": "with open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section\nxmp_start = data.find(b'<?xpacket')\nxmp_end = data.find(b'</x:xmpmeta>') + len(b'</x:xmpmeta>')\nif xmp_start != -1 and xmp_end != -1:\n    xmp = data[xmp_start:xmp_end].decode('utf-8', errors='replace')\n    print(xmp)\nelse:\n    print(\"XMP not found\")\n    # Try to find any text-like content\n    import re\n    text_parts = re.findall(b'[\\x20-\\x7e]{10,}', data)\n    for p in text_parts[:30]:\n        print(p.decode('ascii', errors='replace'))"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 575 ms
```

Recorded observation excerpt:

```text
[step 7] python
exit=0  time=0.57s
--- output ---
<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>
<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Image::ExifTool 10.80'>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>

 <rdf:Description rdf:about=''
  xmlns:cc='http://creativecommons.org/ns#'>
  <cc:license rdf:resource='cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'/>
 </rdf:Description>

 <rdf:Description rdf:about=''
  xmlns:dc='http://purl.org/dc/elements/1.1/'>
  <dc:rights>
   <rdf:Alt>
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>
   </rdf:Alt>
  </dc:rights>
 </rd
```

### Model call 8

Model output (self-reported):

```json
{"thought": "I found a base64-encoded string in the XMP metadata: 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
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
[step 8] decode
exit=0  time=0.00s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 9

Model output (self-reported):

```json
{"thought": "I found the flag by decoding the base64 string in the XMP metadata. The flag is picoCTF{the_m3tadata_1s_modified}.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nflag = base64.b64decode('cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9').decode()\nprint(flag)"}
```

### Model call 10

Model output (self-reported):

```json
{"thought": "The script needs to actually read from the challenge file and extract the base64 string from the XMP metadata, then decode it. Let me write a proper script that does this.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nimport re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP metadata section\nxmp_start = data.find(b'<?xpacket')\nxmp_end = data.find(b'</x:xmpmeta>') + len(b'</x:xmpmeta>')\nif xmp_start != -1 and xmp_end != -1:\n    xmp = data[xmp_start:xmp_end].decode('utf-8', errors='replace')\n    # Find the base64-encoded license resource\n    match = re.search(r\"rdf:resource='([A-Za-z0-9+/=]+)'\", xmp)\n    if match:\n        b64 = match.group(1)\n        flag = base64.b64decode(b64).decode()\n        print(flag)\n    else:\n        print(\"No base64 found in XMP\")\nelse:\n    print(\"XMP not found\")"}
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

# Find the XMP metadata section
xmp_start = data.find(b'<?xpacket')
xmp_end = data.find(b'</x:xmpmeta>') + len(b'</x:xmpmeta>')
if xmp_start != -1 and xmp_end != -1:
    xmp = data[xmp_start:xmp_end].decode('utf-8', errors='replace')
    # Find the base64-encoded license resource
    match = re.search(r"rdf:resource='([A-Za-z0-9+/=]+)'", xmp)
    if match:
        b64 = match.group(1)
        flag = base64.b64decode(b64).decode()
        print(flag)
    else:
        print("No base64 found in XMP")
else:
    print("XMP not found")
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 668 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 10
Wall time: 62.55 s
Input tokens: 50878
Output tokens: 1526
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
