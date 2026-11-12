"""The swap contract, from public values only.

Builds the script and the P2WSH address of either contract so a taker can
say, before anything is locked, exactly where the maker's Marscoin must
appear. Same bytes as Electrum-Mars (atomic_swap_htlc.py create_htlc_script)
and the phone wallet (blue_modules/swap/htlc.js buildHtlcScript).

Public keys and hashes only: nothing here creates, reads or uses a
private key, and nothing builds a transaction.
"""

import hashlib

MARS_HRP = 'mars'
BTC_HRP = 'bc'

OP_IF, OP_ELSE, OP_ENDIF = 0x63, 0x67, 0x68
OP_HASH160, OP_EQUALVERIFY, OP_CHECKSIG = 0xa9, 0x88, 0xac
OP_CHECKLOCKTIMEVERIFY, OP_DROP = 0xb1, 0x75


def _push(data: bytes) -> bytes:
    if not 0 < len(data) < 0x4c:
        raise ValueError('unexpected push length')
    return bytes([len(data)]) + data


def script_number(n: int) -> bytes:
    """Minimal script-number encoding of a positive integer."""
    if not 0 < n < 500_000_000:
        raise ValueError('a locktime must be a block height below 500000000')
    out = n.to_bytes((n.bit_length() + 7) // 8, 'little')
    return out + b'\x00' if out[-1] & 0x80 else out


def _push_number(n: int) -> bytes:
    """As the wallets push it: OP_1..OP_16 for 1..16 (never a real height), else the bytes."""
    encoded = script_number(n)
    return bytes([0x50 + n]) if n <= 16 else _push(encoded)


def htlc_script(payment_hash160: bytes, recipient_pubkey: bytes, sender_pubkey: bytes,
                locktime: int) -> bytes:
    """The recipient claims with the secret; the sender refunds at ``locktime``."""
    if len(payment_hash160) != 20:
        raise ValueError('the payment hash must be 20 bytes')
    for key in (recipient_pubkey, sender_pubkey):
        if len(key) != 33 or key[0] not in (2, 3):
            raise ValueError('public keys must be 33-byte compressed keys')
    return (bytes([OP_IF, OP_HASH160]) + _push(payment_hash160)
            + bytes([OP_EQUALVERIFY]) + _push(recipient_pubkey) + bytes([OP_CHECKSIG, OP_ELSE])
            + _push_number(locktime) + bytes([OP_CHECKLOCKTIMEVERIFY, OP_DROP])
            + _push(sender_pubkey) + bytes([OP_CHECKSIG, OP_ENDIF]))


def output_script(script: bytes) -> bytes:
    """The P2WSH output script paying ``script``."""
    return b'\x00\x20' + hashlib.sha256(script).digest()


def electrum_scripthash(script: bytes) -> str:
    """What blockchain.scripthash.* methods take for the contract's output."""
    return hashlib.sha256(output_script(script)).digest()[::-1].hex()


# ---------------------------------------------------------------- bech32 (BIP173)

_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def _polymod(values) -> int:
    generators = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ value
        for i, g in enumerate(generators):
            if top >> i & 1:
                chk ^= g
    return chk


def _convert_bits(data: bytes, frombits: int, tobits: int) -> list:
    acc = bits = 0
    out = []
    for value in data:
        acc = acc << frombits | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            out.append(acc >> bits & (1 << tobits) - 1)
    if bits:
        out.append(acc << (tobits - bits) & (1 << tobits) - 1)
    return out


def p2wsh_address(script: bytes, hrp: str) -> str:
    """Native segwit (version 0) address of a witness script."""
    data = [0] + _convert_bits(hashlib.sha256(script).digest(), 8, 5)
    expand = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    polymod = _polymod(expand + data + [0] * 6) ^ 1
    checksum = [polymod >> 5 * (5 - i) & 31 for i in range(6)]
    return hrp + '1' + ''.join(_CHARSET[d] for d in data + checksum)


# ---------------------------------------------------------------- the two contracts

def contracts(*, payment_hash160: str, maker_pubkey: str, taker_pubkey: str,
              mars_locktime: int, btc_locktime: int) -> dict:
    """Both contracts of a swap, from hex strings and block heights.

    Marscoin: the taker claims, the maker refunds at ``mars_locktime``.
    Bitcoin: the maker claims, the taker refunds at ``btc_locktime``.
    """
    h = bytes.fromhex(payment_hash160)
    maker, taker = bytes.fromhex(maker_pubkey), bytes.fromhex(taker_pubkey)
    mars = htlc_script(h, taker, maker, mars_locktime)
    btc = htlc_script(h, maker, taker, btc_locktime)
    return {
        'marscoin': {'script_hex': mars.hex(), 'address': p2wsh_address(mars, MARS_HRP),
                     'electrum_scripthash': electrum_scripthash(mars), 'locktime': mars_locktime},
        'bitcoin': {'script_hex': btc.hex(), 'address': p2wsh_address(btc, BTC_HRP),
                    'locktime': btc_locktime},
    }
