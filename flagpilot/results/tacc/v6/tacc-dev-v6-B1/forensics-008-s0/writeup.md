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
{"thought": "Inspect the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 725 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/ && cat ./challenge/exiftool_download_url.txt
exit=0  time=0.72s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 21 05:11 .
drwxrwxr-x 4 root root   4096 Nov 21 05:11 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me check the file type and look for hidden data in the image.", "action": "shell", "cmd": "file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20"}
```

### Tool: shell

```text
Command: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
Exit code: 127
Timed out: False
Duration: 706 ms
```

Recorded observation excerpt:

```text
[step 2] shell: file ./challenge/cat.jpg && xxd ./challenge/cat.jpg | tail -20
exit=127  time=0.71s
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
{"thought": "file command not available. Let me use xxd to inspect the image, and also try strings.", "action": "shell", "cmd": "xxd ./challenge/cat.jpg | tail -30"}
```

### Tool: shell

```text
Command: xxd ./challenge/cat.jpg | tail -30
Exit code: 0
Timed out: False
Duration: 1181 ms
```

Recorded observation excerpt:

```text
[step 3] shell: xxd ./challenge/cat.jpg | tail -30
exit=0  time=1.18s
--- output ---
000d6460: 4ffb c1a1 ff00 f1df fc97 3ffa 981f f783  O.........?.....
000d6470: 43ff 00e3 7ff9 2bff 00ea 624a a32d 05ff  C.....+...bJ.-..
000d6480: 0038 e6dc 1e71 56ff 00bc 0a1f ff00 1bff  .8...qV.........
000d6490: 00c9 73ff 00a9 81ff 0078 143f fe37 ff00  ..s......x.?.7..
000d64a0: 92bf fea6 2494 659f 6de7 052f 0eb1 5a38  ....$.e.m../..Z8
000d64b0: fa87 ff00 c6ff 00f2 97fd 2387 1f50 ff00  ..........#..P..
000d64c0: f8df fe4a ff00 a404 bd26 58cb a3ac 14bb  ...J.....&X.....
000d64d0: e715 cfed ed13 ff00 8dff 00e4
```

### Model call 4

Model output (self-reported):

```json
{"thought": "Let me look for strings in the image and check the beginning of the file for EXIF data.", "action": "shell", "cmd": "strings ./challenge/cat.jpg | grep -i 'pico\\|flag\\|CTF' ; xxd ./challenge/cat.jpg | head -40"}
```

### Tool: shell

```text
Command: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|CTF' ; xxd ./challenge/cat.jpg | head -40
Exit code: 0
Timed out: False
Duration: 901 ms
```

Recorded observation excerpt:

```text
[step 4] shell: strings ./challenge/cat.jpg | grep -i 'pico\|flag\|CTF' ; xxd ./challenge/cat.jpg | head -40
exit=0  time=0.90s
--- output ---
PicoCTF
    <rdf:li xml:lang='x-default'>PicoCTF</rdf:li>
00000000: ffd8 ffe0 0010 4a46 4946 0001 0200 0001  ......JFIF......
00000010: 0001 0000 ffed 0030 5068 6f74 6f73 686f  .......0Photosho
00000020: 7020 332e 3000 3842 494d 0404 0000 0000  p 3.0.8BIM......
00000030: 0013 1c02 7400 0750 6963 6f43 5446 1c02  ....t..PicoCTF..
00000040: 0000 0200 0400 ffe1 0bf9 6874 7470 3a2f  ..........http:/
00000050: 2f6e 732e 6164 6f62 652e 636f 6d2f 7861  /ns.adob
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
Duration: 402 ms
```

Recorded observation excerpt:

```text
[step 5] python
exit=0  time=0.40s
--- output ---
picoCTF{the_m3tadata_1s_modified}

```

### Model call 6

Model output (self-reported):

```json
{"thought": "The flag is decoded from the base64 string in the XMP metadata.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\ns = 'cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9'\nprint(base64.b64decode(s).decode())"}
```

### Model call 7

Model output (self-reported):

```json
{"thought": "The script needs to actually read the file and extract the base64 string from the XMP metadata, not hardcode it.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64, re\n\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Find the base64-encoded string in the XMP metadata (cc:license rdf:resource)\n# The pattern is: rdf:resource='BASE64STRING'/>\nmatch = re.search(rb\"rdf:resource='([A-Za-z0-9+/=]+)'/>\", data)\nif match:\n    b64 = match.group(1).decode()\n    flag = base64.b64decode(b64).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64-looking string that decodes to picoCTF\n    for m in re.finditer(rb\"[A-Za-z0-9+/]{20,}={0,2}\", data):\n        try:\n            decoded = base64.b64decode(m.group(0)).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except Exception:\n            pass"}
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

# Find the base64-encoded string in the XMP metadata (cc:license rdf:resource)
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
Duration: 463 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 7
Wall time: 44.04 s
Input tokens: 18642
Output tokens: 725
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
