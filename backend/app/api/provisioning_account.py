"""Driver-provided vendor account enrollment endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..drivers.onboarding import AccountLogin, OnboardingAccountError
from .provisioning_common import (
    LOCAL_PROVISIONING,
    ProvisioningVendorAccountLoginIn,
    onboarding,
)

router = APIRouter(
    prefix="/api/provisioning/vendor-account",
    tags=["provisioning"],
)


@router.get("/status", dependencies=LOCAL_PROVISIONING)
def provisioning_vendor_account_status(response: Response, driver: str | None = None) -> dict:
    """Report enrollment state without disclosing an account identity or token."""

    provider = onboarding(driver) if driver else onboarding()
    configured = provider.account_configured()
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": provider.provider,
        "configured": configured,
        "renewable_session": configured,
        "vendor_cloud_required": True,
    }


@router.post("/login", dependencies=LOCAL_PROVISIONING)
def provisioning_vendor_account_login(
    body: ProvisioningVendorAccountLoginIn,
    response: Response,
) -> dict:
    """Establish and encrypt a renewable native session without Android or Frida."""

    provider = onboarding(body.driver) if body.driver else onboarding()
    try:
        provider.login(
            AccountLogin(
                account_type=body.account_type,
                account=body.account.strip(),
                password=body.password.get_secret_value(),
                mobile_area=body.mobile_area,
                language=body.language,
                region=body.region,
                area=body.area,
            )
        )
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid vendor account parameters",
                            headers={"Cache-Control": "no-store"}) from None
    except OnboardingAccountError:
        raise HTTPException(status_code=502, detail="vendor account login failed",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "provider": provider.provider,
        "configured": True,
        "renewable_session": True,
    }


@router.post("/refresh", dependencies=LOCAL_PROVISIONING)
def provisioning_vendor_account_refresh(response: Response, driver: str | None = None) -> dict:
    """Renew the encrypted native session without returning credential material."""

    provider = onboarding(driver) if driver else onboarding()
    try:
        provider.refresh_account()
    except LookupError:
        raise HTTPException(status_code=409, detail="vendor account session is unavailable; sign in again",
                            headers={"Cache-Control": "no-store"}) from None
    except OnboardingAccountError:
        raise HTTPException(status_code=502, detail="vendor account refresh failed",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return {
        "provider": provider.provider,
        "configured": True,
        "renewable_session": True,
        "refreshed": True,
    }
