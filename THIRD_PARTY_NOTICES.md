# Third-party notices

Clayrune's own code is under the licence in `LICENSE`. This file retains the
notices that the licences of bundled or required third-party packages ask us to
keep. Pinned versions are in `requirements.txt`.

## py_webauthn (PyPI `webauthn`) 3.0.1 — BSD-3-Clause

Used by `mc/passkeys/` to verify WebAuthn ceremonies.
Source: https://github.com/duo-labs/py_webauthn

```
Copyright (c) 2017-2021 Duo Security, Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Dependencies pulled in with py_webauthn

| Package | Pinned | Licence |
|---|---|---|
| cbor2 | 6.1.5 | MIT |
| pyasn1 | 0.6.4 | BSD-2-Clause |
| pyasn1-modules | 0.4.2 | BSD |
| pyOpenSSL | 26.4.0 | Apache-2.0 |
| cryptography | >=49.0.0 | Apache-2.0 OR BSD-3-Clause |

Each package's own licence file ships in its wheel. A frozen build that bundles
them must carry those files along; retain this list when the set changes.
