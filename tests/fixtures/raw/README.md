# RAW fixtures for dt-cli integration tests

`tests/test_dt_integration.py` skips unless this directory contains a
RAW file (NEF / CR3 / DNG / ARW / RAF / ORF / RW2 / FFF).

We do NOT bundle anyone's RAW for copyright reasons. To run the
integration tests locally:

```sh
# any small RAW file you own; example:
ln -s "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/DSC_4053.NEF" \
    tests/fixtures/raw/sample.NEF
PYTHONPATH=src python3 -m pytest tests/test_dt_integration.py -v
```

The tests verify dt-cli accepts our emitted XMP without silent
substitution (catches HIGH-1 / base64 class bugs from the
adversarial audit) and that EV values actually reach pixel output.
