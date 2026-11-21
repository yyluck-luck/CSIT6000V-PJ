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
{"thought": "Start by inspecting the files in the challenge directory.", "action": "shell", "cmd": "ls -la ./challenge/"}
```

### Tool: shell

```text
Command: ls -la ./challenge/
Exit code: 0
Timed out: False
Duration: 394 ms
```

Recorded observation excerpt:

```text
[step 1] shell: ls -la ./challenge/
exit=0  time=0.39s
--- output ---
total 872
drwxr-xr-x 2 root root   4096 Nov 20 06:05 .
drwxrwxr-x 4 root root   4096 Nov 20 06:05 ..
-r--r--r-- 1 root root 878136 Nov 16 14:02 cat.jpg
-r--r--r-- 1 root root     48 Nov 16 14:02 exiftool_download_url.txt

```

### Model call 2

Model output (self-reported):

```json
{"thought": "Let me check the exiftool download URL and inspect the cat.jpg file for hidden data.", "action": "shell", "cmd": "cat ./challenge/exiftool_download_url.txt"}
```

### Tool: shell

```text
Command: cat ./challenge/exiftool_download_url.txt
Exit code: 0
Timed out: False
Duration: 251 ms
```

Recorded observation excerpt:

```text
[step 2] shell: cat ./challenge/exiftool_download_url.txt
exit=0  time=0.25s
--- output ---
https://exiftool.org/Image-ExifTool-12.64.tar.gz
```

### Model call 3

Model output (self-reported):

```json
{"thought": "Let me inspect the cat.jpg file for hidden data using strings and file type detection.", "action": "strings", "path": "challenge/cat.jpg", "min_len": 6}
```

### Tool: strings

```text
Command: 
Exit code: 0
Timed out: False
Duration: 216 ms
```

Recorded observation excerpt:

```text
[step 3] strings: challenge/cat.jpg
exit=0  time=0.22s
--- output ---
0Photoshop 3.0
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
  <dc:rights>
   <rdf:Alt>
    <rdf:li xm
```

### Model call 4

Model output (self-reported):

```json
{"thought": "I can see a base64-encoded string in the XMP metadata: `cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9`. Let me decode it.", "action": "decode", "scheme": "base64", "text": "cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9"}
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
{"thought": "I found the flag by decoding the base64 string in the XMP metadata of the cat.jpg file.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import base64\nflag = base64.b64decode('cGljb0NURnt0aGVfbTN0YWRhdGFfMXNfbW9kaWZpZWR9').decode()\nprint(flag)"}
```

### Model call 6

Model output (self-reported):

```json
{"thought": "I need to write a script that actually reads the cat.jpg file and extracts the base64 string from the XMP metadata, then decodes it to get the flag.", "action": "submit", "flag": "picoCTF{the_m3tadata_1s_modified}", "script": "import re\nimport base64\n\n# Read the cat.jpg file\nwith open('./challenge/cat.jpg', 'rb') as f:\n    data = f.read()\n\n# Search for base64-encoded strings in the XMP metadata\n# Look for the pattern in cc:license rdf:resource\nmatch = re.search(rb\"rdf:resource='([A-Za-z0-9+/=]+)'\", data)\nif match:\n    b64_str = match.group(1).decode()\n    flag = base64.b64decode(b64_str).decode()\n    print(flag)\nelse:\n    # Fallback: search for any base64-like string that decodes to picoCTF\n    for m in re.finditer(rb\"[A-Za-z0-9+/]{20,}={0,2}\", data):\n        try:\n            decoded = base64.b64decode(m.group()).decode()\n            if 'picoCTF' in decoded:\n                print(decoded)\n                break\n        except:\n            pass"}
```

## Submission

Submission attempts: 2

Submitted flag:

```text
picoCTF{the_m3tadata_1s_modified}
```

Submitted solve.py:

```python
import re
import base64

# Read the cat.jpg file
with open('./challenge/cat.jpg', 'rb') as f:
    data = f.read()

# Search for base64-encoded strings in the XMP metadata
# Look for the pattern in cc:license rdf:resource
match = re.search(rb"rdf:resource='([A-Za-z0-9+/=]+)'", data)
if match:
    b64_str = match.group(1).decode()
    flag = base64.b64decode(b64_str).decode()
    print(flag)
else:
    # Fallback: search for any base64-like string that decodes to picoCTF
    for m in re.finditer(rb"[A-Za-z0-9+/]{20,}={0,2}", data):
        try:
            decoded = base64.b64decode(m.group()).decode()
            if 'picoCTF' in decoded:
                print(decoded)
                break
        except:
            pass
```

## Replay Verification

```text
Verdict: verified
Reason: match
Gold checked: True
Exit code: 0
Duration: 448 ms
Detail: 重放复现出同一个 flag
```

Recorded replay stdout:

```text
picoCTF{the_m3tadata_1s_modified}

```

## Usage

```text
Turns: 6
Wall time: 29.1 s
Input tokens: 9210
Output tokens: 638
Estimated cost: $0.000000
```

Observation and stderr excerpts are limited by the trace recorder. Token/cost figures use recorded application estimates, not a provider invoice.
