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
Duration: 458 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && file ./challenge/cat.jpg && cat ./challenge/exiftool_download_url.txt
exit=127  time=0.46s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 04:07 .
drwxrwxr-x 4 root root   4096 Nov 21 04:07 ..
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
{"thought": "Let me check the file type and look for hidden data in the image.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | head -20 && echo \"---\" && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | head -20 && echo "---" && xxd ./challenge/cat.jpg | tail -20
Exit code: 0
Timed out: False
Duration: 1291 ms
```

Recorded observation excerpt:

```text
[step 2] shell: xxd ./challenge/cat.jpg | head -20 && echo "---" && xxd ./challenge/cat.jpg | tail -20
exit=0  time=1.29s
--- output ---
00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adobe.com/xa
00000060: 702f 312e 302f 003c 3f78 7061 636b 6574  p/1.
```

Recorded stderr excerpt:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe

```

### Model call 3

Model output (self-reported):

```json
{"thought": "I can see 'PicoCTF' in the EXIF data. Let me extract the full EXIF metadata to find the flag.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i \"pico\\|flag\\|ctf\""}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i "pico\|flag\|ctf"
Exit code: 0
Timed out: False
Duration: 419 ms
```

Recorded observation excerpt:

```text
[step 3] shell: strings ./challenge/cat.jpg | grep -i "pico\|flag\|ctf"
exit=0  time=0.42s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>

```

### Model call 4

Model output (self-reported):

```json
{"thought": "The EXIF data shows 'PicoCTF' but not the full flag. Let me look at the full EXIF/XMP metadata more carefully.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | head -100"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | head -100
Exit code: 0
Timed out: False
Duration: 327 ms
```

Recorded observation excerpt:

```text
[step 4] shell: strings ./challenge/cat.jpg | head -100
exit=0  time=0.33s
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

### Model call 5

Model output (self-reported):

```json
{"thought": "I found a base64-encoded string in the EXIF data: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
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
[step 5] decode
exit=0  time=0.00s
--- output ---
picoCTF{the_m3tadata_1s_modified}
```

### Model call 6

Model output (self-reported):

```json
{"thought": "I found the flag. Let me create a standalone script to verify it works.", "action": "run_script", "script": "import base64, re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded flag in the EXIF/XMP metadata\n# The flag is in the cc:license rdf:resource field\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    print(\"Flag not found\")"}
```

### Tool: run_script

```text
Command: 
Exit code: 0
Timed out: False
Duration: 521 ms
```

Recorded observation excerpt:

```text
[step 6] run_script
exit=0  time=0.52s
--- output ---
picoCTF{the_m3tadata_1s_modified}
hint: 脚本在干净目录里跑通了，打印出 1 个 flag 形态。如果这就是你要提交的答案，可以 submit 了。
```

### Model call 7

Model output (self-reported):

```json
{"thought": "The script works correctly and prints the flag. Let me submit it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64, re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded flag in the EXIF/XMP metadata\n# The flag is in the cc:license rdf:resource field\nmatch = re.search(rb'rdf:resource=\\'([A-Za-z0-9+/=]+)\\'', data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    print(\"Flag not found\")"}
```

## Submission

Submission attempts: 1

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import base64, re

with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Find the base64-encoded flag in the EXIF/XMP metadata
# The flag is in the cc:license rdf:resource field
match = re.search(rb'rdf:resource=\'([A-Za-z0-9+/=]+)\'', data)
if match:
    b64 = match.group(1).decode()
    flag = base64.b64decode(b64).decode()
    print(flag)
else:
    print("Flag not found")
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 650 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 7
Wall time: 31.28 s
Input tokens: 21638
Output tokens: 693
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
