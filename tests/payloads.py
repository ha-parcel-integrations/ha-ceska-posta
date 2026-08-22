"""Sample Ceska Posta / Balikovna API payloads shared by the test modules.

Every tracking code, name and address below is invented for these tests — no
real parcel data.
"""
from __future__ import annotations

ACTIVE_CODE = "AB1234567890C"  # out_for_delivery, door-delivery path
DELIVERED_CODE = "CD2345678901D"  # delivered via the door-delivery path
PICKUP_CODE = "EF3456789012F"  # at_pickup_point, Balikovna locker
OTHER_CODE = "GH4567890123H"  # spare code for coordinator/service tests
NOT_FOUND_CODE = "ZZ0000000000Z"  # never resolves to a real consignment


def backbone_event(
    event_id: str,
    date: str,
    text: str = "",
    *,
    id_icon: int | None = None,
    postcode: str | None = None,
    postoffice: str | None = None,
    public_access: int = 1,
) -> dict:
    """One ParcelHistory (Surface A) ``states.state[]`` entry."""
    return {
        "id": event_id,
        "date": date,
        "text": text,
        "postcode": postcode,
        "postoffice": postoffice,
        "idIcon": id_icon,
        "publicAccess": public_access,
        "latitude": None,
        "longitude": None,
        "timeDeliveryAttempt": None,
    }


def enrichment_event(
    state_id: str,
    date: str,
    text: str = "",
    *,
    icon_id: str | None = None,
    time: str | None = None,
    is_public: bool = True,
    zip_code: str | None = None,
    business_name: str | None = None,
) -> dict:
    """One Balikovna (Surface B) ``packageStates[]`` entry."""
    return {
        "stateId": state_id,
        "iconId": icon_id,
        "isPublic": is_public,
        "date": date,
        "time": time,
        "zipCode": zip_code,
        "businessName": business_name,
        "text": text,
    }


def backbone_body(
    tracking_code: str,
    *,
    weight: float | None = 0,
    events: list[dict] | None = None,
    eta_date: str | None = None,
    eta_from: str | None = None,
    eta_to: str | None = None,
) -> dict:
    """A ParcelHistory (Surface A) response object for one parcel."""
    return {
        "id": tracking_code,
        "attributes": {
            "parcelType": tracking_code[:2],
            "weight": weight,
            "currency": "",
            "telefonTyp": "0",
            "telefonNazev": None,
            "telefonCislo": None,
            "dobirka": 0.0,
            "kusu": None,
            "ulozeniDo": None,
            "ulozniDoba": 7,
            "zemePuvodu": None,
            "zemeUrceni": None,
            "dorucovaniDate": eta_date,
            "dorucovaniOd": eta_from,
            "dorucovaniDo": eta_to,
        },
        "states": {"state": events or []},
    }


def enrichment_body(
    tracking_code: str,
    *,
    sender: str | None = "Example Shop",
    recipient: str | None = "Example Recipient",
    recipient_email: str | None = "",
    recipient_phone: str | None = "",
    pickup_place: dict | None = None,
    stored_to: str | None = None,
    status_message: str | None = None,
    events: list[dict] | None = None,
) -> dict:
    """A Balikovna (Surface B) ``package/<code>`` response for one parcel."""
    return {
        "packageId": tracking_code,
        "statusIcon": None,
        "statusMessage": status_message,
        "processingStatus": 0,
        "sender": sender,
        "recipient": recipient,
        "recipientEmail": recipient_email,
        "recipientPhone": recipient_phone,
        "postedTo": "-",
        "storedTo": stored_to,
        "storedToTime": None,
        "deliveryPlace": None,
        "dimensionType": "",
        "cashOnDelivery": 0,
        "currency": "",
        "paymentUrl": None,
        "pickupPlace": pickup_place,
        "packageStates": events or [],
        "indAvizovani": False,
    }


def raw(tracking_code: str, *, backbone: dict | None, enrichment: dict | None) -> dict:
    """The merged raw dict ``normalize_parcel()`` (and the coordinator) consume."""
    return {"id": tracking_code, "backbone": backbone, "enrichment": enrichment}


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A representative door-delivery, fully delivered parcel."""
    events = [
        backbone_event("-M", "2026-04-24", "Receipt of data about consignment."),
        backbone_event(
            "21", "2026-04-25", "The consignment was sent for transportation.", id_icon=2
        ),
        backbone_event(
            "-B",
            "2026-04-26",
            "The consignment is being transported to the delivering post office.",
            id_icon=3,
        ),
        backbone_event(
            "51", "2026-04-27", "The consignment is being prepared for delivery.", id_icon=4
        ),
        backbone_event(
            "53", "2026-04-27", "The consignment is being delivered.", id_icon=5
        ),
        backbone_event("91", "2026-04-27", "The consignment was delivered.", id_icon=8),
    ]
    enrichment_events = [
        enrichment_event(
            "91", "2026-04-27", "The consignment was delivered.", icon_id="8", time="16:42"
        ),
    ]
    backbone = backbone_body(code, weight=1.25, events=events)
    enrichment = enrichment_body(
        code,
        sender="Example Shop",
        recipient="Example Recipient",
        status_message="DELIVERED",
        events=enrichment_events,
    )
    return raw(code, backbone=backbone, enrichment=enrichment)


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """An out-for-delivery parcel, door-delivery path."""
    events = [
        backbone_event("-M", "2026-04-28", "Receipt of data about consignment."),
        backbone_event(
            "21", "2026-04-28", "The consignment was sent for transportation.", id_icon=2
        ),
        backbone_event(
            "-B",
            "2026-04-29",
            "The consignment is being transported to the delivering post office.",
            id_icon=3,
        ),
        backbone_event(
            "51", "2026-04-29", "The consignment is being prepared for delivery.", id_icon=4
        ),
        backbone_event(
            "53", "2026-04-29", "The consignment is being delivered.", id_icon=5
        ),
    ]
    backbone = backbone_body(code, weight=0.9, events=events)
    enrichment = enrichment_body(
        code,
        sender="Example Shop",
        recipient="Example Recipient",
        status_message="OUT FOR DELIVERY",
    )
    return raw(code, backbone=backbone, enrichment=enrichment)


def in_transit_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel that has only just entered the network."""
    events = [
        backbone_event("-M", "2026-04-27", "Receipt of data about consignment."),
        backbone_event(
            "21", "2026-04-28", "The consignment was sent for transportation.", id_icon=2
        ),
    ]
    backbone = backbone_body(code, weight=0.9, events=events)
    enrichment = enrichment_body(
        code, sender="Example Shop", recipient="Example Recipient", status_message="IN TRANSIT"
    )
    return raw(code, backbone=backbone, enrichment=enrichment)


def pickup_sample(code: str = PICKUP_CODE) -> dict:
    """A parcel waiting at a Balikovna locker."""
    events = [
        backbone_event("-M", "2026-04-20", "Receipt of data about consignment."),
        backbone_event(
            "21", "2026-04-21", "The consignment was sent for transportation.", id_icon=2
        ),
        backbone_event(
            "-K", "2026-04-22", "The consignment is being transported to Balikovna.", id_icon=3
        ),
        backbone_event(
            "5K", "2026-04-22", "The consignment is being transported to Balikovna.", id_icon=3
        ),
        backbone_event(
            "P2", "2026-04-22", "The consignment was deposited to Balikovna.", id_icon=7
        ),
    ]
    enrichment_events = [
        enrichment_event(
            "P2",
            "2026-04-22",
            "The consignment was deposited to Balikovna.",
            icon_id="7",
            time="13:17",
        ),
    ]
    pickup_place = {
        "name": "Example Point Central Station",
        "address": "Example street 1, 100 00, Prague",
    }
    backbone = backbone_body(code, weight=0.5, events=events)
    enrichment = enrichment_body(
        code,
        sender="Example Shop",
        recipient="Example Recipient",
        pickup_place=pickup_place,
        stored_to="2026-04-29",
        status_message="WAITING FOR PICKUP",
        events=enrichment_events,
    )
    return raw(code, backbone=backbone, enrichment=enrichment)


def not_found_sample(code: str = NOT_FOUND_CODE) -> dict:
    """The "no such consignment" sentinel on both surfaces."""
    backbone = backbone_body(
        code,
        weight=0,
        events=[
            backbone_event(
                "-3",
                "2026-04-24",
                "There is no record for a consignment with this posting number.",
            )
        ],
    )
    enrichment = enrichment_body(
        code,
        sender="-",
        recipient="",
        recipient_email="",
        recipient_phone="",
        events=[
            enrichment_event(
                "-3",
                "2026-04-24",
                "There is no record for a consignment with this posting number.",
            )
        ],
    )
    return raw(code, backbone=backbone, enrichment=enrichment)
