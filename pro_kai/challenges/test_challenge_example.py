# example challenge: reverse string function + test
def reverse_str(s):
    return s[::-1]

def test_reverse_str():
    assert reverse_str("abc") == "cba"
