import re
import json
import logging
from typing import Text, Optional, List

from flask import Blueprint, request, jsonify, make_response, Response
from slackclient import SlackClient

from rasa_core.channels import InputChannel
from rasa_core.channels.channel import UserMessage, OutputChannel

logger = logging.getLogger(__name__)


class SlackBot(SlackClient, OutputChannel):
    """A Slack communication channel"""

    @classmethod
    def name(cls):
        return "slack"

    def __init__(self,
                 token: Text,
                 slack_channel: Optional[Text] = None) -> None:

        self.slack_channel = slack_channel
        super(SlackBot, self).__init__(token)

    def send_text_message(self, recipient_id, message):
        recipient = self.slack_channel or recipient_id
        print("slack.py send_text_message ::",message)
        for message_part in message.split("\n\n"):
            super(SlackBot, self).api_call("chat.postMessage",
                                           channel=recipient,
                                           as_user=True, text=message_part)

    def send_image_url(self, recipient_id, image_url, message=""):
        image_attachment = [{"image_url": image_url,
                             "text": message}]
        recipient = self.slack_channel or recipient_id
        print("slack.py send_image_url ::",message)
        return super(SlackBot, self).api_call("chat.postMessage",
                                              channel=recipient,
                                              as_user=True,
                                              attachments=image_attachment)

    def send_attachment(self, recipient_id, attachment, message=""):
        recipient = self.slack_channel or recipient_id
        print("slack.py send_attachment ::",message)
        return super(SlackBot, self).api_call("chat.postMessage",
                                              channel=recipient,
                                              as_user=True,
                                              text=message,
                                              attachments=attachment)

    @staticmethod
    def _convert_to_slack_buttons(buttons):
        return [{"text": b['title'],
                 "name": b['payload'],
                 "value": b['payload'],
                 "type": "button"} for b in buttons]

    def send_text_with_buttons(self, recipient_id, message, buttons, **kwargs):
        recipient = self.slack_channel or recipient_id

        if len(buttons) > 5:
            logger.warning("Slack API currently allows only up to 5 buttons. "
                           "If you add more, all will be ignored.")
            return self.send_text_message(recipient, message)

        button_attachment = [{"fallback": message,
                              "callback_id": message.replace(' ', '_')[:20],
                              "actions": self._convert_to_slack_buttons(
                                  buttons)}]
        print("slack.py send_button ::",button_attachment)
        super(SlackBot, self).api_call("chat.postMessage",
                                       channel=recipient,
                                       as_user=True,
                                       text=message,
                                       attachments=button_attachment)


class SlackInput(InputChannel):
    """Slack input channel implementation. Based on the HTTPInputChannel."""

    @classmethod
    def name(cls):
        return "slack"

    @classmethod
    def from_credentials(cls, credentials):
        if not credentials:
            cls.raise_missing_credentials_exception()

        return cls(credentials.get("slack_token"),
                   credentials.get("slack_channel"))

    def __init__(self,
                 slack_token: Text,
                 slack_channel: Optional[Text] = None,
                 errors_ignore_retry: Optional[List[Text]] = None) -> None:
        """Create a Slack input channel.

        Needs a couple of settings to properly authenticate and validate
        messages. Details to setup:

        https://github.com/slackapi/python-slackclient

        Args:
            slack_token: Your Slack Authentication token. You can find or
                generate a test token
                `here <https://api.slack.com/docs/oauth-test-tokens>`_.
            slack_channel: the string identifier for a channel to which
                the bot posts, or channel name (e.g. 'C1234ABC', 'bot-test'
                or '#bot-test') If unset, messages will be sent back
                to the user they came from.
            errors_ignore_retry: If error code given by slack
                included in this list then it will ignore the event.
                The code is listed here:
                https://api.slack.com/events-api#errors
        """
        self.slack_token = slack_token
        self.slack_channel = slack_channel
        self.errors_ignore_retry = errors_ignore_retry or ('http_timeout',)

    @staticmethod
    def _is_user_message(slack_event):
        return (slack_event.get('event') and
                (slack_event.get('event').get('type') == u'message' or
                 slack_event.get('event').get('type') == u'app_mention') and
                slack_event.get('event').get('text') and not
                slack_event.get('event').get('bot_id'))

    @staticmethod
    def _is_button_reply(slack_event):
        pay = json.loads(slack_event['payload']);
        evaluation = (pay['type'] == u"interactive_message") and (pay['actions'][0]['type'] == u"button");
        ## print("Is it a button ? ",evaluation);
        return( evaluation );
        ##        return (slack_event.get('payload') and
        ##                slack_event['payload'][0] and
        ##                'name' in slack_event['payload'][0])

    @staticmethod
    def _get_button_reply(slack_event):
        return json.loads(slack_event['payload'])['actions'][0]['name']
        ## return json.loads(slack_event['payload'][0])['actions'][0]['name']
        

    @staticmethod
    def _sanitize_user_message(text, uids_to_remove):
        """Remove superfluous/wrong/problematic tokens from a message.

        Probably a good starting point for pre-formatting of user-provided text,
        to make NLU's life easier in case they go funky to the power of extreme.

        In the current state will just drop self-mentions of bot itself

        Args:
            text: raw message as sent from slack
            uids_to_remove: a list of user ids to remove from the content

        Returns:
            str: parsed and cleaned version of the input text
        """
        for uid_to_remove in uids_to_remove:
            # heuristic to format majority cases OK
            # can be adjusted to taste later if needed,
            # but is a good first approximation
            for regex, replacement in [(r'<@{}>\s'.format(uid_to_remove), ''),
                                       (r'\s<@{}>'.format(uid_to_remove), ''),
                                       # a bit arbitrary but probably OK
                                       (r'<@{}>'.format(uid_to_remove), ' ')]:
                text = re.sub(regex, replacement, text)

        return text.rstrip().lstrip()  # drop extra spaces at beginning and end

    def process_message(self, on_new_message, text, sender_id):
        """Slack retry to post messages up to 3 times based on
        failure conditions defined here:
        https://api.slack.com/events-api#failure_conditions
        """
        retry_reason = request.headers.environ.get('HTTP_X_SLACK_RETRY_REASON')
        retry_count = request.headers.environ.get('HTTP_X_SLACK_RETRY_NUM')
        ## if retry_count and retry_reason in self.errors_ignore_retry:        
        if retry_count:
            logger.warning("Received retry #{} request from slack"
                           " due to {}".format(retry_count, retry_reason))

            return Response(status=201, headers={'X-Slack-No-Retry': 1})

        try:
            ## print("slack.py here ... L182 ", self.slack_token, " ", self.slack_channel);
            out_channel = SlackBot(self.slack_token, self.slack_channel)
            print("--->> user text ::",retry_count,":: ",text);
            user_msg = UserMessage(text, out_channel, sender_id,
                                   input_channel=self.name())

            on_new_message(user_msg)
        except Exception as e:
            logger.error("Exception when trying to handle "
                         "message.{0}".format(e))
            logger.error(str(e), exc_info=True)

        return make_response()

    def blueprint(self, on_new_message):
        slack_webhook = Blueprint('slack_webhook', __name__)

        @slack_webhook.route("/", methods=['GET'])
        def health():
            return jsonify({"status": "ok"})

        @slack_webhook.route("/webhook", methods=['GET', 'POST'])
        def webhook():
            request.get_data()
            if request.json:
                output = request.json
                if "challenge" in output:
                    return make_response(output.get("challenge"), 200,
                                         {"content_type": "application/json"})
                elif self._is_user_message(output):
                    return self.process_message(
                        on_new_message,
                        text=self._sanitize_user_message(
                            output['event']['text'],
                            output['authed_users']),
                        sender_id=output.get('event').get('user'))
            elif request.form:
                output = dict(request.form)
                if self._is_button_reply(output):
                    ## sender_id = json.loads(output['payload'][0])['user']['id']
                    sender_id = json.loads(output['payload'])['user']['id']
                    return self.process_message(
                        on_new_message,
                        text=self._get_button_reply(output),
                        sender_id=sender_id)

            return make_response()

        return slack_webhook
