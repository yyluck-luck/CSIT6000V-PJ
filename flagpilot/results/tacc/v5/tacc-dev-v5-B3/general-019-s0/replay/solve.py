import os
# The challenge asks to convert 42 (base 10) to binary (base 2)
# The number 42 is given in the challenge description
# We derive the answer by performing the conversion
num = 42
result = bin(num)[2:]
print(f"picoCTF{{{result}}}")