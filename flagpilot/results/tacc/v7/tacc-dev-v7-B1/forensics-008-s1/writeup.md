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
Duration: 889 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.89s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 22 00:54 .
drwxrwxr-x 4 root root   4096 Nov 22 00:54 ..
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
{"thought": "Let me check the exiftool URL and try to use exiftool or other tools to inspect the image.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt && which exiftool strings xxd hexdump 2>/dev/null"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt && which exiftool strings xxd hexdump 2>/dev/null
Exit code: 1
Timed out: False
Duration: 471 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt && which exiftool strings xxd hexdump 2>/dev/null
exit=1  time=0.47s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz/mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool
/usr/bin/strings
/mnt/home/yliutz/WORKDIR/flagpilot-client/bin/xxd

```

### Model call 3

Model output (self-reported):

```json
{"thought": "exiftool is available at /mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool. Let me use it to inspect the image.", "action": "shell", "cmd": "/mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool ./challenge/cat.jpg"}
```

### Tool: shell

```text
Command: /mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool ./challenge/cat.jpg
Exit code: 0
Timed out: False
Duration: 1044 ms
```

Recorded observation excerpt:

```text
[step 3] shell: /mnt/home/yliutz/WORKDIR/flagpilot-client/bin/exiftool ./challenge/cat.jpg
exit=0  time=1.04s
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
Image Heig
```

### Model call 4

Model output (self-reported):

```json
{"thought": "The exiftool shim only shows basic tags. Let me look for hidden data - maybe steganography in the image. Let me check for appended data after the JPEG EOI marker, or look at the strings in the file.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i 'pico\\|flag\\|CTF'"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|CTF'
Exit code: 0
Timed out: False
Duration: 306 ms
```

Recorded observation excerpt:

```text
[step 4] shell: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|CTF'
exit=0  time=0.31s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>

```

### Model call 5

Model output (self-reported):

```json
{"thought": "Let me check if there's data appended after the JPEG end-of-image marker (FFD9). Also let me look at the full strings output and check for any hidden data.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 808 ms
```

Recorded observation excerpt:

```text
[step 5] shell: xxd ./challenge/cat.jpg | tail -20
exit=0  time=0.81s
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

### Model call 6

Model output (self-reported):

```json
{"thought": "The file ends with FFD9 which is the JPEG EOI marker, so no data appended after. Let me look for steganography - maybe the flag is hidden in the LSB of the image, or in the EXIF/XMP data. Let me check the XMP data more carefully.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | head -100"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | head -100
Exit code: 0
Timed out: False
Duration: 496 ms
```

Recorded observation excerpt:

```text
[step 6] shell: strings ./challenge/cat.jpg | head -100
exit=0  time=0.50s
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
  <dc:right
```

### Model call 7

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP data: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "python", "code": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Tool: python

```text
Command: 
Exit code: 0
Timed out: False
Duration: 970 ms
```

Recorded observation excerpt:

```text
[step 7] python
exit=0  time=0.97s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 8

Model output (self-reported):

```json
{"thought": "Found the flag: picoCTF{the_m3tadata_1s_modified}", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 9

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the base64 string from it, not hardcode it. Let me write a script that reads the XMP data from the JPEG file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64, re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the XMP section and extract the base64-encoded license URL\n# The pattern is: <cc:license rdf:resource='BASE64STRING'/>\nmatch = re.search(rb\"<cc:license rdf:resource='([^']+)'/>\", data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for base64 strings that decode to picoCTF\n    for m in re.finditer(rb\"[A-Za-z0-9+/=]{20,}\", data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
```

## Submission

Submission attempts: 2

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import base64, re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the XMP section and extract the base64-encoded license URL
# The pattern is: <cc:license rdf:resource='BASE64STRING'/>
match = re.search(rb"<cc:license rdf:resource='([^']+)'/>", data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    # Fallback: search for base64 strings that decode to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/=]{20,}", data):
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
Duration: 574 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 9
Wall time: 39.59 s
Input tokens: 15893
Output tokens: 940
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
