#!/usr/bin/env python

# Google calendars polling script + audio reminders
# Derived from :
# 	https://developers.google.com/google-apps/calendar/instantiate
# 	https://github.com/ehamiter/get-on-the-bus
#   http://www.oeey.com/2014_10_01_archive.html
# and instanciated as a service/daemon following this:
# 	http://blog.scphillips.com/2013/07/getting-a-python-script-to-run-in-the-background-as-a-service-on-boot/

import gflags
import httplib2
import requests
import time
import os
import logging
import logging.handlers
import sys, traceback
import unicodedata
import pytz

from datetime import datetime, timedelta

from apiclient.discovery import build
from oauth2client.file import Storage
from oauth2client.client import AccessTokenRefreshError
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run_flow
from oauth2client.client import flow_from_clientsecrets

from ConfigParser import SafeConfigParser

from mstranslator import Translator

###########################
# PERSONAL CONFIG FILE READ
###########################

parser = SafeConfigParser()
parser.read('gcalnotifier.ini')

# Read private developer for access to the google API
developerKeyString = parser.get('config', 'developerKey')

# Read key for Microsoft Translate API
microsoftKey = parser.get('config', 'microsoftKey')

# Read list of calendars to be managed concurrently
# NOTE: there is a main calendar, the one with which the credentials have been generated
# Additional calendars must be configured as shared with this main calendar.
calendars = parser.get('config', 'calendars').split(',')

# Read path to log file
LOG_FILENAME = parser.get('config', 'log_filename')

# Read how much time in advance the spoken reminder should be played, if no reminder is specified in gcalendar.
REMINDER_DELTA_DEFAULT = parser.getint('config', 'reminder_minutes_default')

#################
#  LOGGING SETUP
#################
LOG_LEVEL = logging.INFO  # Could be e.g. "DEBUG" or "WARNING"

# Configure logging to log to a file, making a new file at midnight and keeping the last 3 day's data
# Give the logger a unique name (good practice)
logger = logging.getLogger(__name__)
# Set the log level to LOG_LEVEL
logger.setLevel(LOG_LEVEL)
# Make a handler that writes to a file, making a new file at midnight and keeping 3 backups
handler = logging.handlers.TimedRotatingFileHandler(LOG_FILENAME, when="midnight", backupCount=3)
# Format each log message like this
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s')
# Attach the formatter to the handler
handler.setFormatter(formatter)
# Attach the handler to the logger
logger.addHandler(handler)

# Make a class we can use to capture stdout and sterr in the log
class MyLogger(object):
	def __init__(self, logger, level):
		"""Needs a logger and a logger level."""
		self.logger = logger
		self.level = level

	def write(self, message):
		# Only log if there is a message (not just a new line)
		if message.rstrip() != "":
			self.logger.log(self.level, message.rstrip())

# Replace stdout with logging to file at INFO level
sys.stdout = MyLogger(logger, logging.INFO)
# Replace stderr with logging to file at ERROR level
sys.stderr = MyLogger(logger, logging.ERROR)

logger.info('Starting Google Calendar Polling and Notification Service')
logger.info('Using google developerkey %s' % developerKeyString)
logger.info('Using microsoft speed key %s' % microsoftKey)
logger.info('Using calendar list: ' + str(calendars))
logger.info("Beginning authentication...")

###############################
# GOOGLE CALENDAR ACCESS SETUP
###############################

scope = 'https://www.googleapis.com/auth/calendar'
flow = flow_from_clientsecrets('client_secret.json', scope=scope)

storage = Storage('credentials.dat')
credentials = storage.get()

class fakeargparse(object):  # fake argparse.Namespace
 	noauth_local_webserver = True
 	logging_level = "ERROR"
flags = fakeargparse()

if credentials is None or credentials.invalid:
	credentials = run_flow(flow, storage,flags)

# Create an httplib2.Http object to handle our HTTP requests and authorize it
# with our good Credentials.
http = httplib2.Http()
http = credentials.authorize(http)

logger.info("Authentication completed")

# Build a service object for accessing the API
service = build(serviceName='calendar', version='v3', http=http,developerKey=developerKeyString)

###############################
# MICROSOFT SPEECH SETUP
###############################

headers = {"Ocp-Apim-Subscription-Key": microsoftKey}

url = 'https://api.cognitive.microsoft.com/sts/v1.0/issueToken'
r = requests.post(url, data = {'key':'value'}, headers=headers)

logger.info("Microsoft speech access token request returned "+str(r.status_code))

accesstoken = r.text.decode("UTF-8")
logger.info("Access Token: " + accesstoken)

###############################
# MICROSOFT TRANSLATE ACCESS
##############################

def speak(theText):

	f = open("tmp.wav", 'wb')

	body = "<speak version='1.0' xml:lang='fr-FR'><voice xml:lang='fr-FR' xml:gender='Female' name='Microsoft Server Speech Text to Speech Voice (fr-FR, Julie, Apollo)'>"+theText+"</voice></speak>"

	headers = {"Content-type": "application/ssml+xml", 
	            "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm", 
	            "Authorization": "Bearer " + accesstoken, 
	            "X-Search-AppId": "07D3234E49CE426DAA29772419F436CA", 
	            "X-Search-ClientID": "1ECFAE91408841A480F00935DC390960", 
	            "User-Agent": "TTSForPython"}
            
	url = 'https://speech.platform.bing.com/synthesize'

	r = requests.post(url, data = body, headers=headers)
	logger.info("Submitting text to speech (" + theText +") request using access token: " + accesstoken)
	logger.info(str(r.status_code))
	logger.info(r.reason)

	f.write(r.content)
	f.close()

	os.system("aplay tmp.wav")

###############################
# GOOGLE CALENDAR POLLING LOOP
###############################
logger.info("Starting calendars polling & notification loop...")

while True:

	try:

		logger.info("Checking calendars...")

		# get events from calendar, set for the next 30 days
		tzone = pytz.timezone('Europe/Paris')
		now = datetime.now(tz=tzone)

		timeMin = now
		timeMin = timeMin.isoformat()
		timeMax = now + timedelta(days=30)
		timeMax = timeMax.isoformat()

		eventlist = []
		defaultReminderDelta = REMINDER_DELTA_DEFAULT

		# Merge events from all configured calendars
		for calendar in calendars:
				events = service.events().list(singleEvents=True, timeMin=timeMin, timeMax=timeMax, calendarId=calendar).execute()
				if 'items' in events:
					eventlist += events['items']

				# Grab default reminder time value from calendar settings
				if ('defaultReminders' in events) and (len(events['defaultReminders'])>0) :
					defaultReminderDelta = events['defaultReminders'][0]['minutes']

		# Check for each collected event if it is about to start
		for i, event in enumerate(eventlist):

			if 'summary' in event and 'start' in event and 'dateTime' in event['start']:
				# Use this calendar event's summary text as the text to be spoken
				# Also, remove any accentuated characters from the name (too lazy to handle text encoding properly)
				name = unicodedata.normalize('NFKD', event['summary'].lower()).encode('ascii', 'ignore')
				start = event['start']['dateTime'][:-9]
				description = event.get('description', '')
				repeat = True if description.lower() == 'repeat' else False

				# By default, set announce time to (event start time) - (default value from config or from calendar itself)
				# Unless some specific reminders are specified in the event
				reminder_deltatime = defaultReminderDelta
				if 'reminders' in event:
					reminders = event['reminders']

					if reminders['useDefault'] == False:
						# Parse overridden reminders to get time value
						if 'overrides' in reminders:
							for override in reminders['overrides']:
								if 	override['method'] == 'popup':
									reminder_deltatime = override['minutes']
									break;

				logger.info('Event #%s, Name: %s, Start: %s, Reminder at %d minutes', i, name, start, reminder_deltatime)

				# If the start time of the event is reached, play out a speech synthesis corresponding to the event
				expiration = now + timedelta(minutes=reminder_deltatime)
				if start == expiration.strftime('%Y-%m-%dT%H:%M'):
					
					# send a (simulated) IR command to the audio controller, so that it can prepare for sound output (mute ongoing music or just turn on amplifier)
					os.system('irsend simulate "0000000000004660 0 KEY_START_ANNOUNCE piremote"')
					
					# play "start of announce" jingle
					time.sleep(1)
					os.system('aplay audio_on.wav')

					# Speak the calendar entry text
					logger.info('Event starting in %d minutes. Announcing \'%s\'...', reminder_deltatime, name)
					speak(name)

					# Speak "I repeat,"
					speak("je raipaite")
					
					# Speak the calendar entry text again
					speak(name)

					# play "end of announce" jingle
					time.sleep(1)
					os.system('aplay audio_off.wav')
					time.sleep(1)

					# send a (simulated) IR command to the audio controller, so that it can resume its music playback (or just turn off again)
					os.system('irsend simulate "0000000000022136 0 KEY_END_ANNOUNCE piremote"')
					
					if repeat == False:
						# wait until the current minute ends, so as not to re-trigger this event, if no repeat condition is specified
						time.sleep(60)

		# Poll calendar every 30 seconds
		time.sleep(30)

	except:
		logger.info("*****Exception in main loop, retrying in 30 seconds ******")
		exc_type, exc_value, exc_traceback = sys.exc_info()
		traceback.print_exception(exc_type, exc_value, exc_traceback,limit=2, file=sys.stdout)	
		del exc_traceback
		time.sleep(30)
		continue
