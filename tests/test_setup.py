import pytest

from atomicswapd.service import setup_stage

SYNCED = {'wallet_ready': True, 'wallet_synced': True, 'local_height': 3_584_540,
          'server_height': 3_584_542, 'wallet_error': None}


def maker(node=SYNCED, inventory=3000, plan=True, offers=False, error=None):
    return {'node': dict(node), 'error': error,
            'balance': {'inventory': inventory, 'deposit_address': 'mars1qdeposit'},
            'plan': [{'tier': 1}] if plan else [], 'offers': [{'id': 'x'}] if offers else []}


def stage(m, mode='watch', payout_set=True):
    return setup_stage(m, mode=mode, payout_set=payout_set, cap_percent=30)


def test_needs_wallet():
    s = stage(maker(node={'wallet_ready': False, 'wallet_error': 'no wallet yet (run: ...)'}))
    assert s['stage'] == 'needs_wallet' and '--seed-file' in s['next']


def test_wallet_error_is_reported():
    s = stage(maker(node={'wallet_ready': False, 'wallet_error': 'could not open the wallet: bad password'}))
    assert s['stage'] == 'wallet_error' and 'bad password' in s['next']


def test_syncing_headers_and_wallet():
    s = stage(maker(node={**SYNCED, 'local_height': 1_792_271}))
    assert s['stage'] == 'syncing' and s['progress'] == 50.0
    assert stage(maker(node={**SYNCED, 'wallet_synced': False}))['stage'] == 'syncing'


@pytest.mark.parametrize('inventory', [0, 500])
def test_needs_funds_with_the_real_minimum(inventory):
    s = stage(maker(inventory=inventory))
    assert s['stage'] == 'needs_funds' and 'mars1qdeposit' in s['next']
    assert s['min_inventory_mars'] == 667     # 200 MARS minimum / 30% cap


def test_needs_payout_address():
    assert stage(maker(), payout_set=False)['stage'] == 'needs_payout_address'


def test_ready_then_live():
    assert stage(maker(plan=False))['stage'] == 'waiting_for_price'
    ready = stage(maker())
    assert ready['stage'] == 'ready' and 'ATOMICSWAPD_MODE=live' in ready['next']
    assert stage(maker(), mode='live')['stage'] == 'live'
    assert stage(maker(offers=True), mode='live')['next'] == 'Live and posting offers.'


def test_maker_error_blocks():
    assert stage(maker(error='boom'))['stage'] == 'maker_error'
