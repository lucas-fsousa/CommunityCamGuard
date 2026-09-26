"""Fixed public errors for identification and privileged onboarding boundaries."""

from enum import Enum

from fastapi import HTTPException


class EnrollmentFailure(Enum):
    DRIVER = (422, "selected driver does not support factory onboarding")
    LABEL = (422, "invalid camera label; check the QR code, device ID, capability code and MAC address")
    SESSION = (409, "onboarding session is unavailable; restart setup for this camera")
    BIND = (409, "camera enrollment is not ready; check the account and obtain a fresh Wi-Fi setup handoff")
    STATE = (409, "camera enrollment is not ready; check the account and durable enrollment material")
    TRANSPORT = (502, "camera enrollment communication failed; check connectivity and account status before retrying")
    COMPLETE = (502, "camera setup could not be completed; check enrollment, LAN connectivity and media configuration before retrying")


def enrollment_failure(kind: EnrollmentFailure) -> HTTPException:
    # Only caller-owned enum values enter this projection, never exceptions/stages.
    status, message = kind.value
    return HTTPException(
        status_code=status, detail=message,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )
