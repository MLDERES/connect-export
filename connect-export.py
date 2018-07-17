import requests # MD using requests instead of urllib for Python 3 support
from datetime import datetime
from getpass import getpass
from sys import argv
from os.path import isdir
from os.path import isfile
from os import mkdir
from xml.dom.minidom import parseString
import pprint

# Maximum number of activities you can request at once.  Set and enforced by Garmin.
limit_maximum = 100
DEBUG=True
# URLs for various services.
url_gc_login     = 'https://sso.garmin.com/sso/login?service=https%3A%2F%2Fconnect.garmin.com%2Fpost-auth%2Flogin&webhost=olaxpw-connect04&source=https%3A%2F%2Fconnect.garmin.com%2Fen-US%2Fsignin&redirectAfterAccountLoginUrl=https%3A%2F%2Fconnect.garmin.com%2Fpost-auth%2Flogin&redirectAfterAccountCreationUrl=https%3A%2F%2Fconnect.garmin.com%2Fpost-auth%2Flogin&gauthHost=https%3A%2F%2Fsso.garmin.com%2Fsso&locale=en_US&id=gauth-widget&cssUrl=https%3A%2F%2Fstatic.garmincdn.com%2Fcom.garmin.connect%2Fui%2Fcss%2Fgauth-custom-v1.1-min.css&clientId=GarminConnect&rememberMeShown=true&rememberMeChecked=false&createAccountShown=true&openCreateAccount=false&usernameShown=false&displayNameShown=false&consumeServiceTicket=false&initialFocus=true&embedWidget=false&generateExtraServiceTicket=false'
url_gc_post_auth = 'https://connect.garmin.com/post-auth/login?'
url_gc_search    = 'http://connect.garmin.com/proxy/activity-search-service-1.0/json/activities?'
url_gc_activity  = 'http://connect.garmin.com/proxy/activity-service-1.1/gpx/activity/'

url_gc_activity_download = 'https://connect.garmin.com/modern/proxy/download-service/export/gpx/activity/' # /activityid e.g./2850825962

class SessionCache(dict):
	
	def __init__(self):
		#super(SessionCache,self).__init__()
		pass
	
	def Get(self, key):
		if (key in self.keys()):
			return self[key]
		else:
			return None

	def Set(self, key, value):
		self[key]=value


class GarminConnect():
	_obligatory_headers = {"Referer": "http://info.com"}
	
	def __init__(self):
		self._sessionCache = SessionCache()

	def _rate_limit(self):
		return

	# Borrowed from https://github.com/cpfair/tapiriik/blob/master/tapiriik/services/GarminConnect/garminconnect.py
	def _get_session(self, record=None, email=None, password=None, skip_cache=False):
		#from tapiriik.auth.credential_storage import CredentialStore
		cached = self._sessionCache.Get(record.ExternalID if record else email)
		if cached and not skip_cache:
				logger.debug("Using cached credential")
				return cached
		if record:
			password = record.password #CredentialStore.Decrypt(record.ExtendedAuthorization["Password"])
			email = record.email #CredentialStore.Decrypt(record.ExtendedAuthorization["Email"])

		session = requests.Session()

		# JSIG CAS, cool I guess.
		# Not quite OAuth though, so I'll continue to collect raw credentials.
		# Commented stuff left in case this ever breaks because of missing parameters...
		data = {
			"username": email,
			"password": password,
			"_eventId": "submit",
			"embed": "true",
			# "displayNameRequired": "false"
		}
		params = {
			"service": "https://connect.garmin.com/modern",
			# "redirectAfterAccountLoginUrl": "http://connect.garmin.com/modern",
			# "redirectAfterAccountCreationUrl": "http://connect.garmin.com/modern",
			# "webhost": "olaxpw-connect00.garmin.com",
			"clientId": "GarminConnect",
			"gauthHost": "https://sso.garmin.com/sso",
			# "rememberMeShown": "true",
			# "rememberMeChecked": "false",
			"consumeServiceTicket": "false",
			# "id": "gauth-widget",
			# "embedWidget": "false",
			# "cssUrl": "https://static.garmincdn.com/com.garmin.connect/ui/src-css/gauth-custom.css",
			# "source": "http://connect.garmin.com/en-US/signin",
			# "createAccountShown": "true",
			# "openCreateAccount": "false",
			# "usernameShown": "true",
			# "displayNameShown": "false",
			# "initialFocus": "true",
			# "locale": "en"
		}
		# I may never understand what motivates people to mangle a perfectly good protocol like HTTP in the ways they do...
		preResp = session.get("https://sso.garmin.com/sso/login", params=params)
		if preResp.status_code != 200:
			raise APIException("SSO prestart error %s %s" % (preResp.status_code, preResp.text))

		ssoResp = session.post("https://sso.garmin.com/sso/login", params=params, data=data, allow_redirects=False)
		if ssoResp.status_code != 200 or "temporarily unavailable" in ssoResp.text:
			raise APIException("SSO error %s %s" % (ssoResp.status_code, ssoResp.text))

		if ">sendEvent('FAIL')" in ssoResp.text:
			raise APIException("Invalid login", block=True, user_exception=UserException(UserExceptionType.Authorization, intervention_required=True))
		if ">sendEvent('ACCOUNT_LOCKED')" in ssoResp.text:
			raise APIException("Account Locked", block=True, user_exception=UserException(UserExceptionType.Locked, intervention_required=True))

		if "renewPassword" in ssoResp.text:
			raise APIException("Reset password", block=True, user_exception=UserException(UserExceptionType.RenewPassword, intervention_required=True))

		# ...AND WE'RE NOT DONE YET!

		self._rate_limit()
		gcRedeemResp = session.get("https://connect.garmin.com/modern", allow_redirects=False)
		if gcRedeemResp.status_code != 302:
			raise APIException("GC redeem-start error %s %s" % (gcRedeemResp.status_code, gcRedeemResp.text))
		url_prefix = "https://connect.garmin.com"
		# There are 6 redirects that need to be followed to get the correct cookie
		# ... :(
		max_redirect_count = 7
		current_redirect_count = 1
		while True:
    		
			self._rate_limit()
			url = gcRedeemResp.headers["location"]
			# Fix up relative redirects.
			if url.startswith("/"):
				url = url_prefix + url
			url_prefix = "/".join(url.split("/")[:3])
			gcRedeemResp = session.get(url, allow_redirects=False)

			if current_redirect_count >= max_redirect_count and gcRedeemResp.status_code != 200:
				raise APIException("GC redeem %d/%d error %s %s" % (current_redirect_count, max_redirect_count, gcRedeemResp.status_code, gcRedeemResp.text))
			if gcRedeemResp.status_code == 200 or gcRedeemResp.status_code == 404:
				break
			current_redirect_count += 1
			if current_redirect_count > max_redirect_count:
				break

		self._sessionCache.Set(record.ExternalID if record else email, session)

		session.headers.update(self._obligatory_headers)

		return session

	def Authorize(self, email, password):
			#from tapiriik.auth.credential_storage import CredentialStore
			session = self._get_session(email=email, password=password, skip_cache=True)
			self._rate_limit()
			try:
				dashboard = session.get("http://connect.garmin.com/modern")
				userdata_json_str = re.search(r"VIEWER_SOCIAL_PROFILE\s*=\s*JSON\.parse\((.+)\);$", dashboard.text, re.MULTILINE).group(1)
				userdata = json.loads(json.loads(userdata_json_str))
				username = userdata["displayName"]
			except Exception as e:
				raise APIException("Unable to retrieve username: %s" % e, block=True, user_exception=UserException(UserExceptionType.Authorization, intervention_required=True))
			return (username, {}, {"Email": email, "Password": password})

gc = GarminConnect()
print(gc.Authorize(email='mlderes@hotmail.com',password='W@termel0n_'))
