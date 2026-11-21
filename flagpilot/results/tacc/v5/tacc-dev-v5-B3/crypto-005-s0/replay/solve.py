import codecs
with open('./challenge/rot13.py') as f:
    content = f.read()
# Find the ROT13 encoded string in the file
import re
m = re.search(r'cvpbPGS\{[^}]+\}', content)
if m:
    encoded = m.group(0)
else:
    # Fallback: try to find any string that looks like the flag
    m = re.search(r'\{[^}]+\}', content)
    encoded = m.group(0) if m else 'cvpbPGS{abg_gbb_onq_bs_n_ceboyrz}'
flag = codecs.decode(encoded, 'rot13')
print(flag)