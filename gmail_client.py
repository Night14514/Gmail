import os
import json
import hashlib
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

SEARCH_SCOPE = "in:anywhere"

@dataclass
class AccountInfo:
    email: str
    token_path: str
    account_id: str = ""
    status: str = "unknown"  # "active" or "error"
    credentials: Optional[Credentials] = None
    service: Optional[Any] = None

class GmailClient:
    def __init__(self, tokens_dir: str = "tokens", auto_load: bool = True):
        self.tokens_dir = tokens_dir
        self.accounts: Dict[str, AccountInfo] = {}
        self.id_to_email: Dict[str, str] = {}
        self.email_to_id: Dict[str, str] = {}
        if auto_load:
            self._load_accounts()

    def _make_account_id(self, email: str) -> str:
        return hashlib.md5(email.encode()).hexdigest()[:8]

    def _register_account_id(self, email: str) -> str:
        account_id = self._make_account_id(email)
        self.email_to_id[email] = account_id
        self.id_to_email[account_id] = email
        return account_id

    def resolve_email(self, account_id_or_email: str) -> Optional[str]:
        if account_id_or_email in self.id_to_email:
            return self.id_to_email[account_id_or_email]
        if account_id_or_email in self.accounts:
            return account_id_or_email
        return None

    def get_account_id(self, email: str) -> Optional[str]:
        return self.email_to_id.get(email)

    def _load_accounts(self) -> None:
        if not os.path.exists(self.tokens_dir):
            os.makedirs(self.tokens_dir, exist_ok=True)
            return

        for filename in os.listdir(self.tokens_dir):
            if filename.startswith("token_") and filename.endswith(".json"):
                token_path = os.path.join(self.tokens_dir, filename)
                try:
                    with open(token_path, 'r') as f:
                        token_data = json.load(f)
                    
                    creds = Credentials.from_authorized_user_info(token_data)
                    
                    service = build('gmail', 'v1', credentials=creds)
                    profile = service.users().getProfile(userId='me').execute()
                    email = profile['emailAddress']
                    account_id = self._register_account_id(email)
                    
                    self.accounts[email] = AccountInfo(
                        email=email,
                        token_path=token_path,
                        account_id=account_id,
                        status="active",
                        credentials=creds,
                        service=service
                    )
                except (RefreshError, HttpError) as e:
                    print(f"Error loading account from {filename}: {e}")
                    continue
                except Exception as e:
                    print(f"Unexpected error loading account from {filename}: {e}")
                    continue

    def _refresh_credentials(self, account: AccountInfo) -> bool:
        try:
            if account.credentials and account.credentials.expired:
                account.credentials.refresh(Request())
                self._save_token(account)
                account.status = "active"
                return True
            return True
        except (RefreshError, HttpError) as e:
            account.status = "error"
            print(f"Failed to refresh credentials for {account.email}: {e}")
            return False
        except Exception as e:
            account.status = "error"
            print(f"Unexpected error refreshing credentials for {account.email}: {e}")
            return False

    def _save_token(self, account: AccountInfo) -> None:
        if account.credentials:
            with open(account.token_path, 'w') as f:
                f.write(account.credentials.to_json())

    def _ensure_valid_credentials(self, account: AccountInfo) -> bool:
        if account.status == "error":
            return False
        
        if not account.credentials:
            return False
        
        try:
            if account.credentials.expired:
                return self._refresh_credentials(account)
            return True
        except Exception as e:
            account.status = "error"
            print(f"Error checking credentials for {account.email}: {e}")
            return False

    def health_check(self, email: str) -> bool:
        if email not in self.accounts:
            return False
        
        account = self.accounts[email]
        try:
            if not self._ensure_valid_credentials(account):
                return False
            
            service = build('gmail', 'v1', credentials=account.credentials)
            service.users().getProfile(userId='me').execute()
            account.status = "active"
            return True
        except Exception as e:
            account.status = "error"
            print(f"Health check failed for {email}: {e}")
            return False

    def get_messages(
        self,
        email: str,
        max_results: int = 5,
        page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        if email not in self.accounts:
            return {"error": "Account not found"}
        
        account = self.accounts[email]
        
        if not self._ensure_valid_credentials(account):
            return {"error": "Invalid credentials"}
        
        try:
            service = build('gmail', 'v1', credentials=account.credentials)
            list_kwargs: Dict[str, Any] = {
                "userId": "me",
                "q": SEARCH_SCOPE,
                "maxResults": max_results,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            
            result = service.users().messages().list(**list_kwargs).execute()
            
            account.status = "active"
            return result
        except HttpError as e:
            account.status = "error"
            print(f"HTTP error getting messages for {email}: {e}")
            return {"error": str(e)}
        except Exception as e:
            account.status = "error"
            print(f"Error getting messages for {email}: {e}")
            return {"error": str(e)}

    def get_messages_with_metadata(
        self,
        email: str,
        max_results: int = 5,
        page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        if email not in self.accounts:
            return {"error": "Account not found"}
        
        account = self.accounts[email]
        
        if not self._ensure_valid_credentials(account):
            return {"error": "Invalid credentials"}
        
        try:
            service = build('gmail', 'v1', credentials=account.credentials)
            list_kwargs: Dict[str, Any] = {
                "userId": "me",
                "q": SEARCH_SCOPE,
                "maxResults": max_results,
            }
            if page_token:
                list_kwargs["pageToken"] = page_token
            
            list_result = service.users().messages().list(**list_kwargs).execute()
            messages = list_result.get('messages', [])
            next_page_token = list_result.get('nextPageToken')
            
            if not messages:
                account.status = "active"
                return {"messages": [], "nextPageToken": next_page_token}
            
            metadata_by_id: Dict[str, Dict[str, str]] = {}
            
            def batch_callback(request_id, response, exception):
                if exception is not None:
                    print(f"Batch metadata error for {request_id}: {exception}")
                    return
                msg_id = response.get('id', '')
                headers = {
                    h['name'].lower(): h['value']
                    for h in response.get('payload', {}).get('headers', [])
                }
                metadata_by_id[msg_id] = {
                    "id": msg_id,
                    "from": headers.get('from', ''),
                    "subject": headers.get('subject', '(Без темы)'),
                }
            
            batch = service.new_batch_http_request(callback=batch_callback)
            for msg in messages:
                batch.add(
                    service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='metadata',
                        metadataHeaders=['From', 'Subject']
                    )
                )
            batch.execute()
            
            ordered = []
            for msg in messages:
                msg_id = msg['id']
                ordered.append(
                    metadata_by_id.get(msg_id, {
                        "id": msg_id,
                        "from": "",
                        "subject": "(Без темы)",
                    })
                )
            
            account.status = "active"
            return {"messages": ordered, "nextPageToken": next_page_token}
        except HttpError as e:
            account.status = "error"
            print(f"HTTP error getting messages with metadata for {email}: {e}")
            return {"error": str(e)}
        except Exception as e:
            account.status = "error"
            print(f"Error getting messages with metadata for {email}: {e}")
            return {"error": str(e)}

    def get_message(
        self,
        email: str,
        message_id: str,
        format: str = "full",
        metadata_headers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if email not in self.accounts:
            return {"error": "Account not found"}
        
        account = self.accounts[email]
        
        if not self._ensure_valid_credentials(account):
            return {"error": "Invalid credentials"}
        
        try:
            service = build('gmail', 'v1', credentials=account.credentials)
            get_kwargs: Dict[str, Any] = {
                "userId": "me",
                "id": message_id,
                "format": format,
            }
            if format == "metadata" and metadata_headers:
                get_kwargs["metadataHeaders"] = metadata_headers
            message = service.users().messages().get(**get_kwargs).execute()
            
            account.status = "active"
            return message
        except HttpError as e:
            account.status = "error"
            print(f"HTTP error getting message for {email}: {e}")
            return {"error": str(e)}
        except Exception as e:
            account.status = "error"
            print(f"Error getting message for {email}: {e}")
            return {"error": str(e)}

    def get_latest_from_sender(self, email: str, sender_email: str) -> Optional[Dict[str, Any]]:
        """Return latest message metadata from sender in one account, or None."""
        result = self.search_messages(email, f"from:{sender_email}", max_results=1)
        if "error" in result:
            return None
        messages = result.get("messages") or []
        if not messages:
            return None
        msg_id = messages[0]["id"]
        message = self.get_message(
            email,
            msg_id,
            format="metadata",
            metadata_headers=["From", "Subject", "Date"],
        )
        if "error" in message:
            return None
        return {
            "account_email": email,
            "message_id": msg_id,
            "message": message,
            "internal_date": int(message.get("internalDate", 0)),
        }

    def search_messages(self, email: str, query: str, max_results: int = 10) -> Dict[str, Any]:
        if email not in self.accounts:
            return {"error": "Account not found"}
        
        account = self.accounts[email]
        
        if not self._ensure_valid_credentials(account):
            return {"error": "Invalid credentials"}
        
        try:
            service = build('gmail', 'v1', credentials=account.credentials)
            full_query = f"{SEARCH_SCOPE} {query}"
            
            result = service.users().messages().list(
                userId='me',
                q=full_query,
                maxResults=max_results
            ).execute()
            
            account.status = "active"
            return result
        except HttpError as e:
            account.status = "error"
            print(f"HTTP error searching messages for {email}: {e}")
            return {"error": str(e)}
        except Exception as e:
            account.status = "error"
            print(f"Error searching messages for {email}: {e}")
            return {"error": str(e)}

    def get_account_status(self, email: str) -> str:
        if email not in self.accounts:
            return "not_found"
        return self.accounts[email].status

    def get_all_accounts(self) -> Dict[str, AccountInfo]:
        return self.accounts

    def reload_accounts(self) -> None:
        self.accounts.clear()
        self.id_to_email.clear()
        self.email_to_id.clear()
        self._load_accounts()
