"""Lossless columnar packing of the big payload tables.

A table is a list of equal-length integer rows. Sorted row by row and stored column by column, with neighbouring
values replaced by their difference wherever that makes the column more repetitive, it gzips to less than half the size.
The browser unpacks it again on load (decodeColumnar in index.html); the dashboard code never sees the packed form.

Packed table: {"_col": 1, "n": <rows>, "d": [<0|1 per column: 1 = difference from the previous value>], "c": [<column>, ...]}
Row order is not preserved (rows are sorted); every consumer sums rows, so the order carries no meaning.
"""

MIN_ROWS = 20000     # smaller tables gain nothing worth the extra moving part


def encode_table(rows):
    rows = sorted(rows)
    n = len(rows)
    flags, cols_out = [], []
    for col in zip(*rows):
        col = list(col)
        if all(type(v) is int for v in col):
            diff = [col[0]] + [col[i] - col[i - 1] for i in range(1, n)]
            if len(set(diff)) < len(set(col)):
                flags.append(1)
                cols_out.append(diff)
                continue
        flags.append(0)
        cols_out.append(col)
    return {'_col': 1, 'n': n, 'd': flags, 'c': cols_out}


def decode_table(t):
    cols = []
    for flag, col in zip(t['d'], t['c']):
        if flag:
            acc, out = 0, []
            for v in col:
                acc += v
                out.append(acc)
            col = out
        cols.append(col)
    return [list(r) for r in zip(*cols)]


def _packable(v):
    return (isinstance(v, list) and len(v) >= MIN_ROWS and all(isinstance(r, list) for r in v)
            and len({len(r) for r in v}) == 1 and len(v[0]) > 0)


def encode_payload(payload):
    """Pack every large table of the payload in place. Returns {key: rows} for the tables packed."""
    done = {}
    for k, v in list(payload.items()):
        if not _packable(v):
            continue
        try:
            payload[k] = encode_table(v)
            done[k] = len(v)
        except TypeError:        # rows that cannot be ordered (mixed types): leave the table as it is
            payload[k] = v
    return done
