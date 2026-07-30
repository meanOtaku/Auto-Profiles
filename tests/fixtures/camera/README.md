# Synthetic Camera Fixtures

These fixtures are generated geometric pixel fields and contain no person, face, biometric data, or third-party media.

| File | Purpose | Shape / frames | SHA-256 |
|---|---|---|---|
| `synthetic.png` | Image-source decoding | 32×24 split black/white image | `0be35ea4c760625c8791ef729b0c55e68111fec4720830291f5f8939ce1c5738` |
| `synthetic.avi` | Video sequencing and clean termination | Two 32×24 MJPEG frames: black, then white | `19eb51c2c5c1afa278eb491fdbe3150148b7d452a0ac00f1b9ef368d14745072` |

They were generated locally with the exact locked `opencv-python-headless` dependency. Tests treat them as immutable, versioned inputs. Regeneration intentionally changes the checksum and requires review of both the fixture and this file.
