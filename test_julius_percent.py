"""'X% of Y' means X/100*Y, both in the calculator and in direct spoken-math routing."""
import unittest
from julius_calculator import evaluate,normalize_percent
from julius_spoken_math import arithmetic_expression
import julius_tools as jt
class PercentTests(unittest.TestCase):
 def test_percent_of_is_x_over_100_times_y(self):
  for text,want in [('17.5% of 2480',434),('17.5 percent of 2480',434),('50 per cent of 80',40),('10% of 1,250',125),('200% of 7',14),('0% of 99',0)]:
   self.assertAlmostEqual(evaluate(text),want,msg=text)
 def test_normalization_is_explicit(self):
  self.assertEqual(normalize_percent('17.5% of 2480'),'(17.5/100)*(2480)')
 def test_percent_of_parenthesised_expression_and_inside_larger_expression(self):
  self.assertAlmostEqual(evaluate('15% of (200 + 50)'),37.5)
  self.assertAlmostEqual(evaluate('100 + 10% of 100'),110)
 def test_bare_percent_remains_modulo(self):
  self.assertEqual(evaluate('17 % 5'),2)
  self.assertEqual(normalize_percent('17 % 5'),'17 % 5')
 def test_tool_reports_the_percentage_not_a_product(self):
  self.assertEqual(jt.calculate('17.5% of 2480'),'17.5% of 2480 = 434')
 def test_tool_schema_teaches_percent_syntax(self):
  desc=next(s for s in jt.SCHEMAS if s['function']['name']=='calculate')['function']['description']
  self.assertIn('X% of Y',desc)
 def test_spoken_percentage_is_routed_directly(self):
  self.assertEqual(arithmetic_expression('What is 17.5 percent of 2480?'),'17.5/100*2480')
  self.assertEqual(evaluate(arithmetic_expression('what is twenty percent of fifty')),10)
  self.assertEqual(arithmetic_expression('what is 15% of 1,200?'),'15/100*1200')
 def test_percentage_discussion_is_not_executed(self):
  self.assertIsNone(arithmetic_expression('what percent of voters are undecided'))
  self.assertIsNone(arithmetic_expression('what is 50 percent of the budget'))
if __name__=='__main__':unittest.main()
