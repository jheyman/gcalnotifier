#!/bin/bash
# the -ao option is added to explicitly specify which sound output to use, in case multiple DACs are present
 #e.g. : -ao alsa:device=hw=1.0
say() { local IFS=+;/usr/bin/mplayer -really-quiet -noconsolecontrols "http://translate.google.com/translate_tts?tl=fr&q=$*"; }
say $*
