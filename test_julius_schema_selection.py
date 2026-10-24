"""Only the tools a request could use are offered to the model; unmatched requests keep them all."""
import unittest
import julius_tools as jt
def names(text):return {s['function']['name'] for s in jt.select_schemas(text)}
class SchemaSelectionTests(unittest.TestCase):
 def test_weather_gets_weather_tools_only(self):
  self.assertEqual(names("What's the weather like in Springfield?"),{'get_weather','get_forecast'})
 def test_percentage_math_gets_calculator(self):
  self.assertIn('calculate',names('What is 17.5 percent of 2480?'))
  self.assertNotIn('get_weather',names('What is 17.5 percent of 2480?'))
 def test_prices_scores_time_and_files(self):
  self.assertEqual(names("What's Bitcoin trading at?"),{'get_quote'})
  self.assertIn('get_scores',names('Who won the Giants game yesterday?'))
  self.assertIn('get_datetime',names('What time is it in Tokyo?'))
  self.assertEqual(names('List the files in my Downloads folder'),{'list_files'})
  self.assertIn('read_file',names('Read the file notes.txt'))
  self.assertIn('find_files',names('Find the file named budget'))
 def test_multiple_intents_union(self):
  self.assertTrue({'get_weather','get_quote'}<=names('weather and the bitcoin price'))
 def test_unmatched_request_keeps_full_catalogue(self):
  self.assertEqual(jt.select_schemas('Do the thing'),jt.SCHEMAS)
  self.assertEqual(jt.select_schemas(''),jt.SCHEMAS)
 def test_selected_schemas_are_much_smaller_than_the_catalogue(self):
  import json
  self.assertLess(len(json.dumps(jt.select_schemas("What's the weather?"))),len(json.dumps(jt.SCHEMAS))/3)
 def test_every_candidate_tool_hint_selects_something_specific(self):
  for t in ['weather','forecast','what day is it','price of AAPL','sports scores','calculate 4 plus 5','search the news','read the file','list the folder']:
   self.assertLess(len(jt.select_schemas(t)),len(jt.SCHEMAS),t)
if __name__=='__main__':unittest.main()
