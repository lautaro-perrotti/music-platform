# FAST_LAB: Main capture has silent right channel

Observed on 2026-10-02 in the disposable 125 BPM drum lab. The original
`capture_c0704ec47194.wav` is 44.1 kHz stereo, 7.68 s, with left RMS
0.04570 and right RMS 0. The second capture has the same left-only behavior.
Both decode and contain non-silent music, but a single right-ear monitor will
hear silence. `CAPTURE_QUALITY_WARNING` also remains for temporal alignment.

Preserve raw captures. Review-only copies duplicate left into right without
normalization; they are never labeled as raw DAW evidence. Investigate the
capture path separately before claiming stereo or sample-accurate parity.
