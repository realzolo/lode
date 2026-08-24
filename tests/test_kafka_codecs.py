"""Runtime codec capabilities required by the Kafka ingestion contract."""

from aiokafka.codec import has_snappy, snappy_decode, snappy_encode


def test_snappy_codec_is_available() -> None:
    """Consumers must be able to decode batches compressed by Kafka producers."""
    assert has_snappy()
    payload = b'{"specVersion":"alert.v1","message":"connection refused"}'
    assert snappy_decode(snappy_encode(payload)) == payload
