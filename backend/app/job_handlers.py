from typing import Any, Dict, Callable
import asyncio
import httpx
from pydantic import BaseModel, EmailStr, HttpUrl, Field

# 1. Pydantic Validation Schemas per Job Type
class EmailSendPayload(BaseModel):
    to_email: EmailStr
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)

class ReportGenerationPayload(BaseModel):
    report_type: str = Field(pattern="^(summary|detailed|audit)$")
    format: str = Field(default="pdf", pattern="^(pdf|csv|json)$")
    include_metrics: bool = True

class DataSyncPayload(BaseModel):
    source_table: str
    dest_table: str
    batch_size: int = Field(default=100, gt=0, le=1000)

class HttpRequestPayload(BaseModel):
    url: str  # We accept string for direct processing, validate inside
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|DELETE)$")
    headers: Dict[str, str] = Field(default_factory=dict)
    body: str = Field(default="")

# Dispatch dict for validation models
VALIDATORS: Dict[str, Any] = {
    "email_send": EmailSendPayload,
    "report_generation": ReportGenerationPayload,
    "data_sync": DataSyncPayload,
    "http_request": HttpRequestPayload
}

# 2. Execution logic per job type
async def handle_email_send(payload: Dict[str, Any], log_cb: Callable[[str, str], None]) -> Dict[str, Any]:
    data = EmailSendPayload(**payload)
    await log_cb("info", f"Initiating email dispatch to {data.to_email}")
    await asyncio.sleep(1.5)  # Simulate SMTP latency
    await log_cb("info", f"Email successfully sent to {data.to_email} with subject: '{data.subject}'")
    return {"status": "sent", "recipient": data.to_email}

async def handle_report_generation(payload: Dict[str, Any], log_cb: Callable[[str, str], None]) -> Dict[str, Any]:
    data = ReportGenerationPayload(**payload)
    await log_cb("info", f"Starting {data.report_type} report generation in {data.format} format")
    for i in range(1, 4):
        await asyncio.sleep(1.0)  # Simulate work
        await log_cb("info", f"Generating report chunks: {i*33}% complete")
    await log_cb("info", f"Report compiled successfully.")
    return {"status": "generated", "type": data.report_type, "format": data.format, "url": f"https://s3.amazonaws.com/reports/{uuid_generator()}"}

async def handle_data_sync(payload: Dict[str, Any], log_cb: Callable[[str, str], None]) -> Dict[str, Any]:
    data = DataSyncPayload(**payload)
    await log_cb("info", f"Starting sync from {data.source_table} to {data.dest_table}")
    await asyncio.sleep(2.0)
    await log_cb("info", f"Synced 452 rows from {data.source_table} to {data.dest_table}")
    return {"status": "synced", "rows_count": 452}

async def handle_http_request(payload: Dict[str, Any], log_cb: Callable[[str, str], None]) -> Dict[str, Any]:
    data = HttpRequestPayload(**payload)
    await log_cb("info", f"Sending {data.method} request to {data.url}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            if data.method == "GET":
                response = await client.get(data.url, headers=data.headers)
            elif data.method == "POST":
                response = await client.post(data.url, headers=data.headers, content=data.body)
            elif data.method == "PUT":
                response = await client.put(data.url, headers=data.headers, content=data.body)
            elif data.method == "DELETE":
                response = await client.delete(data.url, headers=data.headers)
            
            status_code = response.status_code
            await log_cb("info", f"HTTP {data.method} request returned status code: {status_code}")
            
            # Raise exception if non-2xx status code
            if status_code >= 400:
                raise Exception(f"HTTP error status: {status_code}")
                
            return {
                "status_code": status_code,
                "response": response.text[:1000]  # First 1k characters
            }
        except httpx.RequestError as exc:
            await log_cb("error", f"An error occurred while requesting {exc.request.url!r}.")
            raise exc

HANDLERS: Dict[str, Callable[[Dict[str, Any], Callable[[str, str], None]], Any]] = {
    "email_send": handle_email_send,
    "report_generation": handle_report_generation,
    "data_sync": handle_data_sync,
    "http_request": handle_http_request
}

def uuid_generator() -> str:
    import uuid
    return str(uuid.uuid4())

async def execute_job(job_type: str, payload: Dict[str, Any], log_cb: Callable[[str, str], None]) -> Dict[str, Any]:
    """
    Main entry point for executing jobs with Pydantic payload validation.
    
    log_cb is an async function: log_cb(level: str, message: str)
    """
    if job_type not in HANDLERS:
        err_msg = f"Unknown job type: '{job_type}'. No handler registered."
        await log_cb("error", err_msg)
        raise ValueError(err_msg)
        
    validator = VALIDATORS.get(job_type)
    if validator:
        try:
            # Validate payload
            validator(**payload)
            await log_cb("info", f"Payload validation succeeded for job type '{job_type}'")
        except Exception as e:
            err_msg = f"Payload validation failed for job type '{job_type}': {str(e)}"
            await log_cb("error", err_msg)
            raise ValueError(err_msg)
            
    handler = HANDLERS[job_type]
    return await handler(payload, log_cb)
