import os
# Read challenge files to derive the answer
# The challenge asks to convert 42 (base 10) to binary
# We read the challenge directory to confirm the challenge exists
challenge_dir = './challenge'
files = os.listdir(challenge_dir)
assert len(files) > 0, "No challenge files found"
# The number to convert is 42 (from the challenge description)
# Convert to binary
result = bin(42)[2:]
print(f"picoCTF{{{result}}}")