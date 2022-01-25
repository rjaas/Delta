# Rasa Action Serveri käivitatavad käsud
import os
import sys
import json
import psycopg2
import datetime
from PIL import Image, ImageDraw
from num2words import num2words

from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction, AllSlotsReset, SessionStarted, ActionExecuted

sys.path.append("../")


# Andmebaasi seaded
from auxiliary.database_settings import *

# Nädalapäevade nimetused
WEEKDAYS = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}


class ActionResetAllSlots(Action):
    def name(self) -> Text:
        return "reset_all_slots"

    def run(self,
            dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        return [AllSlotsReset()]


class ActionUtterGeneralHelp(Action):
    def name(self) -> Text:
        return "display_general_help"

    def run(self,
            dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        dispatcher.utter_message(text='This is the general help message.')
        dispatcher.utter_message(text='To learn about my competencies, you can ask me "What can you do?".')
        dispatcher.utter_message(text='For help on employee office searches, you can ask me "How to search for employee offices?"')
        dispatcher.utter_message(text='For help on course event data searches, you can ask me "How to search for course events?"')

        return []


class ActionSearchOffices(Action):
    def name(self) -> Text:
        return "office_search"

    def run(self,
            dispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        if type(tracker.get_slot('employee')) != str:
            return [FollowupAction(name="utter_office_unrecognized_name")]
        name = tracker.get_slot('employee').title()
        conn = psycopg2.connect(host=DATABASE_HOST, port=DATABASE_PORT, database=DATABASE_NAME, user=DATABASE_USER, password=DATABASE_PASSWORD)
        cur = conn.cursor()

        # If exact matching fails, offer to repeat query with closest match
        # https://www.postgresql.org/docs/13/pgtrgm.html
        cur.execute("SELECT room_nr FROM offices WHERE name = '" + name + "';")
        result = cur.fetchone()
        if result is None:
            cur.execute("SELECT * FROM offices WHERE name % '" + name + "' ORDER BY name DESC;")
            result = cur.fetchone()
            cur.close()
            conn.close()
            return [SlotSet("office_search_result", result[1])]

        cur.close()
        conn.close()
        if result[0] is None:
            return []
        return [SlotSet("office_search_result", result[0])]


# Deprecated by fuzzy matching extractor
class ActionParseEmployeeSuggestionReply(Action):
    def name(self) -> Text:
        return "office_search_parse_suggestion_reply"

    def run(self,
            dispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        if tracker.get_slot("office_employee_suggestion_feedback"):
            conn = psycopg2.connect(host=DATABASE_HOST, port=DATABASE_PORT, database=DATABASE_NAME, user=DATABASE_USER, password=DATABASE_PASSWORD)
            cur = conn.cursor()
            cur.execute("SELECT room_nr FROM offices WHERE name = '" + tracker.get_slot("office_search_result") + "';")
            result = cur.fetchone()[0]
            cur.close()
            conn.close()
            return [SlotSet("office_search_result", result),
                    FollowupAction("utter_office_result")
                    ]

        else:
            dispatcher.utter_message(text="Alright.")
            return [AllSlotsReset(),
                    FollowupAction("utter_offer_additional_help")
                    ]


# TODO: Only limited course titles are added to the lookup table.
#  Memorize all unique course titles and add responses for irrelevant ones.
class ActionCourseEventResponse(Action):

    def name(self) -> Text:
        return "course_event_data_response"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        course_title = tracker.get_slot("course")
        course_event = tracker.get_slot("course_event")

        conn = psycopg2.connect(host=DATABASE_HOST, port=DATABASE_PORT, database=DATABASE_NAME, user=DATABASE_USER, password=DATABASE_PASSWORD)
        cur = conn.cursor()

        cur.execute("SELECT week_nr FROM ut_weeks WHERE monday <= '" + str(datetime.date.today())
                    + "' AND sunday >= '" + str(datetime.date.today()) + "';")
        current_week = str(cur.fetchone()[0])

        cur.execute("SELECT weekday, begin_time, end_time, location, notes_et, notes_en FROM course_events WHERE course_title_en = '"
                    + course_title.replace("'", "''")
                    + "' AND week = " + current_week
                    + " AND event_type_en = '" + course_event
                    + "';")

        results = cur.fetchall()

        if len(results) == 0:
            dispatcher.utter_message("I don't know of any " + course_title + " " + course_event + "s this week.")
        else:
            result_count = len(results)
            dispatcher.utter_message(course_title + " has " + str(result_count) + " " + course_event
                                     + ("s" if result_count > 1 else "") + " this week.")
            dispatcher.utter_message("\n")
            for i in range(len(results)):
                result = results[i]

                response_string = ""

                # Sensible sentence starts
                if len(results) == 1:
                    response_string += "It"
                else:
                    response_string += "The " + num2words(i+1, to='ordinal')

                # Location
                if result[3] is None:
                    response_string += " has no designated location. It takes place "
                elif "Narva mnt 18" not in result[3]:
                    response_string += " takes place in " + result[3] + ", "
                else:
                    response_string += " takes place in room " + result[3].split(" - ")[1] + ", "
                # Time
                response_string += "on " + WEEKDAYS[result[0]] + " between " + str(result[1]).rsplit(":", 1)[0] + " and " + str(result[2]).rsplit(":", 1)[0] + "."

                if result[4] != 'NULL':
                    response_string += " The following (probably Estonian) note is attached: "
                    response_string += result[4]
                if result[5] != 'NULL':
                    response_string += " The following note is attached: "
                    response_string += result[5]
                dispatcher.utter_message(response_string)

        cur.close()
        conn.close()

        return [AllSlotsReset(),
                FollowupAction("utter_offer_additional_help")]


class ActionDrawLocationMap(Action):
    def name(self) -> Text:
        return "draw_location_map"

    def run(self,
            dispatcher: "CollectingDispatcher",
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        room_nr = tracker.get_slot("office_search_result")
        if room_nr is None:
            room_nr = tracker.get_slot("room_of_interest")

        if str(room_nr) + ".png" not in os.listdir("../../media/location_images/"):
            with open("../../delta_map/delta_pixel_map.json") as f:
                pixel_map = json.load(f)
            # if f"delta_{room_nr // 1000}.png" not in os.listdir("../auxiliary/delta_map/") or str(room_nr) not in pixel_map.keys() or pixel_map[str(room_nr)] == []:
            #     dispatcher.utter_message("Sorry, I don't have the mapping for that room just yet.")
            #     return [AllSlotsReset(),
            #             FollowupAction("utter_offer_additional_help")]
            center = pixel_map[str(room_nr)]
            img = Image.open(f"../../delta_map/delta_{room_nr // 1000}.png")
            ImageDraw.Draw(img).ellipse([center[0]-5, center[1]-5, center[0]+5, center[1]+5], fill=(255, 0, 0))
            img.save(f"../../media/location_images/{room_nr}.png")
        dispatcher.utter_message(f"!img /media/location_images/{room_nr}.png")

        return []


def room_has_mapping(room_nr):
    if str(room_nr) + ".png" not in os.listdir("../../media/location_images/"):
        with open("../../delta_map/delta_pixel_map.json") as f:
            pixel_map = json.load(f)
        if f"delta_{room_nr // 1000}.png" not in os.listdir("../../delta_map/") or str(room_nr) not in pixel_map.keys() or pixel_map[str(room_nr)] == []:
            return False
    return True


class ActionCheckRoomMapping(Action):
    def name(self) -> Text:
        return "check_room_mapping"

    def run(self,
            dispatcher: "CollectingDispatcher",
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        room_nr = tracker.get_slot("office_search_result")
        if room_nr is None:
            room_nr = tracker.get_slot("room_of_interest")

        return [SlotSet("room_is_mapped", room_has_mapping(room_nr))]
