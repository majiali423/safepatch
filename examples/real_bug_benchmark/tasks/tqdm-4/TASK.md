# tqdm bug 4

Numeric unit scaling must tolerate an iterable whose total length is unknown.
The public and hidden checks use different scale factors and iteration counts.
