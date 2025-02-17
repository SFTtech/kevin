import hmac
from hashlib import sha1


def verify_secret(blob, headers, secret):
    """
    verify the github hmac signature with our shared secret.
    """

    localsignature = hmac.new(secret, blob, sha1)
    goodsig = 'sha1=' + localsignature.hexdigest()
    msgsig = headers.get("X-Hub-Signature")
    if not msgsig:
        raise ValueError("message doesn't have a signature.")

    if hmac.compare_digest(msgsig, goodsig):
        return True

    return False
