
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field



class LeadStatus(str, Enum):
    NEW                    = "NEW"
    ORIENTATION_SCHEDULED  = "ORIENTATION_SCHEDULED"
    ORIENTATION_ATTENDED   = "ORIENTATION_ATTENDED"
    APPOINTMENT_SCHEDULED  = "APPOINTMENT_SCHEDULED"
    CONVERTED              = "CONVERTED"
    REORIENTATION_REQUIRED = "REORIENTATION_REQUIRED"
    DORMANT                = "DORMANT"


class LeadSource(str, Enum):
    WEBSITE     = "WEBSITE"
    INSTAGRAM   = "INSTAGRAM"
    FACEBOOK    = "FACEBOOK"
    YOUTUBE     = "YOUTUBE"
    WALK_IN     = "WALK_IN"
    REFERRAL    = "REFERRAL"
    CALL_CENTER = "CALL_CENTER"
    WHATSAPP    = "WHATSAPP"
    OTHER       = "OTHER"


class InterestedIn(str, Enum):
    CONSULTATION = "CONSULTATION"
    DEVICE       = "DEVICE"
    BOTH         = "BOTH"



class LeadCreate(BaseModel):
    name:          str          = Field(..., min_length=2, max_length=200)
    phone:         str          = Field(..., min_length=7,  max_length=20)
    email:         Optional[str]  = None
    lead_source:   LeadSource     = LeadSource.OTHER
    interested_in: InterestedIn   = InterestedIn.CONSULTATION
    notes:         Optional[str]  = None


class LeadStatusUpdate(BaseModel):
    status: LeadStatus
    notes:  Optional[str] = None



class LeadResponse(BaseModel):
    id:                   str
    name:                 str
    phone:                str
    email:                Optional[str]     = None
    lead_source:          Optional[str]     = None
    interested_in:        Optional[str]     = None
    status:               str               = LeadStatus.NEW
    notes:                Optional[str]     = None
    erp_patient_id:       Optional[str]     = None
    created_at:           Optional[datetime] = None
    modified_at:          Optional[datetime] = None
    appointment_eligible: bool              = False

    @classmethod
    def from_erp(cls, data: dict) -> "LeadResponse":
        status = data.get("status", LeadStatus.NEW)
        return cls(
            id=data.get("name", ""),
            name=data.get("lead_name") or data.get("name", ""),
            phone=data.get("mobile_number") or data.get("mobile_no") or data.get("phone", ""),
            email=data.get("email_id"),
            lead_source=data.get("lead_source"),
            interested_in=data.get("interested_in"),
            status=status,
            notes=data.get("notes"),
            erp_patient_id=data.get("erp_patient_id"),
            created_at=data.get("creation"),
            modified_at=data.get("modified"),
            appointment_eligible=(status == LeadStatus.ORIENTATION_ATTENDED),
        )