"""Text for the synthetic wake-word dataset. Positives never use Julius's own voice (it is a hard negative, see build_dataset.py)."""
BARE = ["Julius.", "Hey Julius.", "Okay Julius.", "Hey, Julius.", "Julius?", "Yo Julius."]
REQUESTS = [
    "what's the weather in Springfield?", "what time is it?", "remind me to call my mom at three forty five.", "who won the game last night?",
    "what's Ethereum trading at?", "what's on my calendar tomorrow?", "set a timer for ten minutes.", "how are you doing?", "read me the news.",
    "what's seventeen percent of twenty four eighty?", "convert twenty six miles to kilometers.", "is it going to rain tomorrow?",
    "can you help me with something?", "turn that down a little.", "what's the score of the Sharks game?", "tell me a joke.",
    "how long is the drive to Sacramento?", "what's the capital of Australia?", "play something relaxing.", "stop.", "never mind.",
    "organize my downloads folder.", "what did I ask you yesterday?", "wake up.", "are you there?",
]
PREFIXES = ["Julius, ", "Hey Julius, ", "Okay Julius, ", "Julius "]
# Words and names that sound like the wake word and must not fire it
SOUNDALIKES = ["Julia.", "Hey Julia.", "Julian.", "Hey Julian.", "Jealous.", "Julie.", "Hey Julie.", "Jules.", "Hey Jules.", "July.", "Jewel.", "Jealousy.",
               "Julianne.", "Hey Julianna.", "Gillian.", "Hey Jillian.", "Joel.", "Hey Joel.", "Jules, come here.", "Ulysses.", "Hey, Judas.", "Julio.", "Hey Julio."]
SOUNDALIKE_SENTENCES = [
    "Julia said she would call me back tomorrow.", "I asked Julian to bring the folding chairs.", "He got a little jealous when she mentioned the trip.",
    "We went to see my aunt Julie on Sunday.", "Jules and I are going hiking this weekend.", "Julia left her keys in the car again.",
    "It was a hot day in July, and nobody wanted to cook.", "Hey Julia, did you finish the report?", "Julian, do you have a minute?",
    "She wore a beautiful jewel around her neck.", "Don't be jealous, there's plenty for everyone.", "Julio is bringing the guitar tonight.",
    "I think Gillian might have the answer.", "The jury will decide on Monday.", "That joke was truly a jewel of comedy.", "Hey Julie, what time does the movie start?",
]
GENERAL = [
    "The coffee machine downstairs makes a terrible noise.", "Can you pick up the dry cleaning on the way home?", "I keep meaning to organize my photos.",
    "The meeting got moved to Thursday afternoon.", "Do you think the new pizza place is any good?", "He told me the car needs new brakes.",
    "We drove up the coast and stopped at every farm stand.", "My phone battery barely lasts until lunch.", "Let's meet at noon and decide where to eat.",
    "I read that you should water tomatoes in the morning.", "The train was delayed again this morning.", "I'd love to learn how to bake bread.",
    "She laughed so hard she almost spilled her tea.", "The thermostat never holds a steady temperature.", "Do you want me to grab anything from the store?",
    "The farmers market had the best peaches all summer.", "We watched the sunset from the roof.", "My laptop fan has been loud since the update.",
    "The movie was too long but the ending made up for it.", "I need to renew my passport before the trip.", "Our flight lands at six tonight.",
    "The plant on the windowsill needs a bigger pot.", "It was a long week and I just want a quiet evening.", "They changed the password policy again.",
    "That podcast about the history of bridges was fascinating.", "I always forget how steep the hill is.", "Somebody left the garage door open overnight.",
    "What's the weather going to be like this weekend?", "Please remind me to call the dentist.", "What time does the game start?",
    "How many calories are in an avocado?", "Add milk and eggs to the shopping list.", "Turn off the lights in the kitchen.", "Is the library open on Sundays?",
    "Hey, can you hear me okay?", "Hey there, how was your day?", "Okay, let's get started.", "Okay, that makes sense to me.", "Hey, did you see the game last night?",
    "Yeah, I'll be there around six.", "Whose turn is it to do the dishes?", "I think we should leave a little early.", "She said the interview went well.",
    "Alexa, what's the weather?", "Hey Siri, set an alarm.", "Hey Google, play some music.", "Okay Google, call mom.", "Hey Jarvis, status report.",
    "The package should arrive sometime between Tuesday and Friday.", "I tried a recipe for lemon chicken last night.", "He fixed the squeaky door with a little oil.",
    "We should get a bigger table for when everyone comes over.", "The kids are at their grandmother's house until Sunday.", "I had to reschedule the dentist appointment.",
    "There's a strange smell coming from the refrigerator.", "My brother said the trailhead was closed.", "It's supposed to get cold this weekend.",
    "The bookshelf is finally organized by color.", "The audio on the video call kept cutting out.", "We were halfway to the airport when I forgot my charger.",
    "A little patience goes a long way.", "Nobody told me the store closes at eight.", "I'll have the soup and a small salad, please.", "Could you say that one more time?",
]
# Julius's own voice must never wake him: his name in his own mouth, and his usual replies
JULIUS_OWN = [
    "Julius.", "Hey Julius.", "I'm Julius.", "This is Julius.", "Julius here.", "My name is Julius.", "Hey, I'm Julius, your assistant.", "Julius is ready.",
    "Okay, Julius is listening.", "You can call me Julius.", "Yes, it's Julius. What can I do for you?", "Julius at your service.", "Hi the user, it's Julius.",
    "I'm Julius, and I'm here.", "Hey the user. Julius here. What do you need?", "Good morning. Julius reporting in.", "Sure. Julius can do that.",
    "It's seventy two degrees in Springfield, and sunny.", "Your timer is set for ten minutes.", "Okay, I'll remind you at three forty five.",
    "The Sharks won three to two in overtime.", "Ethereum is trading around two thousand dollars.", "Sorry, I didn't catch that. Could you say it again?",
    "I'm not sure about that one, let me check.", "Here's what I found.", "Good evening. Anything else I can help with?",
]
# Held-out lines for the eval "Julius's own voice never wakes him" (not in JULIUS_OWN, so never trained on)
JULIUS_OWN_EVAL = [
    "Julius.", "Hey Julius.", "Hello, I'm Julius.", "Julius can look that up for you.", "Hey the user, Julius here, how can I help?", "That's Julius, signing off.",
    "Okay, Julius is on it.", "You asked Julius for the weather, and it's sixty eight degrees.", "My name's Julius, nice to meet you.", "Sure thing. This is Julius.",
    "Julius, that's me. What do you need?", "It's Julius again, with your reminder.", "Hey, Julius here. Your timer is done.", "Okay Julius is ready when you are.",
    "Yes, I'm Julius.", "Hey the user, I'm Julius.", "The weather in Springfield is clear, says Julius.", "Julius is checking now.",
]

# Templated sound-alike sentences: names that sound like the wake word, at the start, middle and end of ordinary speech
_NAMES = ["Julia", "Julian", "Julie", "Jules", "Julio", "Julianna", "Jillian", "Gillian", "Juliette"]
_TEMPLATES = [
    "{n} left her umbrella at the office again.", "I asked {n} to bring the folding chairs.", "{n}, do you have a minute?", "Hey {n}, did you finish the report?",
    "Okay {n}, let's get started.", "Did you see what {n} posted last night?", "{n} said she would call me back tomorrow.", "We're having dinner with {n} on Friday.",
    "Tell {n} the meeting moved to Thursday.", "I think {n} has the answer.", "{n} just adopted a rescue cat.", "Have you met {n}'s new roommate?",
    "It was {n} who left the garage door open.", "Yeah, {n} is coming over around six.", "Say hi to {n} for me.", "{n} and I are going hiking this weekend.",
    "Hey {n}, what time does the movie start?", "Can you pass the salt, {n}?", "I borrowed this book from {n}.", "What do you think, {n}?",
    "{n}'s birthday is coming up and nobody has a gift idea.", "I was talking to {n} about the trip yesterday.", "Where did {n} put the car keys?", "Good morning, {n}.",
    "{n} is learning to play the cello.", "Remind {n} to send the slides tonight.", "Okay, {n}, I'll be right there.", "I haven't heard from {n} since March.",
]
_JEALOUS = ["I'm not jealous, I'm just saying some people have all the luck.", "He gets a little jealous whenever I mention the lake house.",
            "Don't be jealous, there's plenty for everyone.", "She was jealous of the view from their balcony.", "A little jealousy never hurt anyone.",
            "You'd be jealous if you saw the kitchen.", "Jealous much?", "The jury is still out on the new schedule.", "That joke was a real jewel.",
            "He was jealous of the attention she got.", "They're just jealous because we got the best seats."]
def soundalike_sentences():
    out = [t.format(n=n) for n in _NAMES for t in _TEMPLATES] + _JEALOUS
    return out
