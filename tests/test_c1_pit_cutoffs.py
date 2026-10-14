"""Date-only snapshots must not accidentally pretend to support intraday data."""
import pytest

from mozes.pit import LeakageError, assert_point_in_time, before, snapshot_at, thaw


EVENT = {'id': 'FROZEN', 'ticker': 'FIX', 'prices': [
    {'date': '2021-01-14', 'close': 10}, {'date': '2021-01-15', 'close': 999}],
    'bench': [{'date': '2021-01-15', 'close': 888}],
    'documents': [{'published': None, 'text': 'unknown source'},
                  {'published': '2021-01-16', 'text': 'later revision'}]}


@pytest.mark.parametrize('cutoff', ['2021-01-15T09:00:00-05:00', '2021-01-15T15:59:59-05:00',
                                  '2021-01-15T21:00:00Z', '2021-01-15T22:00:00Z',
                                  '2021-01-16T00:30:00+03:00', '2021-01-15T09:00:00'])
def test_intraday_and_afterhours_snapshots_fail_closed_without_market_availability(cutoff):
    with pytest.raises(ValueError, match='intraday_snapshot_unsupported'):
        snapshot_at(EVENT, cutoff)


@pytest.mark.parametrize('cutoff', [None, '', 'garbage', '2021-02-30', '20210115', '2021-W02-5'])
def test_invalid_snapshot_cutoff_is_rejected_before_comparison(cutoff):
    with pytest.raises(ValueError, match='invalid_as_of'):
        snapshot_at(EVENT, cutoff)


def test_date_only_snapshot_keeps_tminus1_semantics_and_frozen_identity():
    snap = snapshot_at(EVENT, '2021-01-15')
    assert snap['id'] == 'FROZEN' and [p['close'] for p in snap['prices']] == [10]
    assert snap['bench'] == () and snap['documents'] == ()
    with pytest.raises(TypeError):
        snap['prices'][0]['close'] = 500
    assert EVENT['prices'][0]['close'] == 10


def test_defense_in_depth_rejects_changed_intraday_cutoff():
    snap = thaw(snapshot_at(EVENT, '2021-01-15'))
    snap['as_of'] = '2021-01-15T09:00:00-05:00'
    snap['prices'].append({'date': '2021-01-15', 'close': 999})
    with pytest.raises(LeakageError):
        assert_point_in_time(snap)


def test_invalid_or_unavailable_price_dates_are_not_compared_as_strings():
    event = {**EVENT, 'prices': EVENT['prices'] + [{'close': 123},
        {'date': '', 'close': 456}, {'date': '2021-01-14T09:00:00', 'close': 789}]}
    assert [p['close'] for p in snapshot_at(event, '2021-01-15')['prices']] == [10]


def test_publication_timestamp_cannot_lose_its_timezone_or_revision_availability():
    event = {**EVENT, 'documents': [
        {'published': '2021-01-14T23:30:00-05:00', 'text': 'not available at UTC cutoff'},
        {'published': '2021-01-14T22:00:00Z', 'text': 'known before UTC cutoff'},
        {'published': '2021-01-14T12:00:00', 'text': 'timezone unknown'},
        {'published': None, 'text': 'publication unknown'},
        {'published': '2021-01-16', 'text': 'later revised text'}]}
    assert [d['text'] for d in snapshot_at(event, '2021-01-15')['documents']] == ['known before UTC cutoff']


@pytest.mark.parametrize('publication', [None, '', 'garbage', '2021-13', '2021-02-30'])
def test_invalid_source_availability_fails_closed(publication):
    assert before(publication, '2021-01-15') is False
