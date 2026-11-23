import unittest

from collectors.companies.sources import ru_sanctions as R

SDN = ('100,"JSC ACME MISSILE PLANT",-0- ,"RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
       '"Tax ID No. 7701234567 (Russia); Organization Type: Manufacture of weapons and ammunition"\n'
       '101,"IVANOV, Ivan",individual,"RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
       '102,"BANK ONE",-0- ,"RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n')
ADD = ('100,1,"ul. Lenina 1","Tula 300000","Russia",-0- \n101,2,-0- ,"Moscow","Russia",-0- \n'
       '102,3,-0- ,"Riga","Latvia",-0- \n')
UK = '''<Designations><Designation><UniqueID>RUS1</UniqueID><Names><Name><Name6>Kalashnikov Concern</Name6>
<NameType>Primary name</NameType></Name></Names><NonLatinNames><NonLatinName><NameNonLatinScript>Концерн Калашников</NameNonLatinScript>
</NonLatinName></NonLatinNames><RegimeName>The Russia (Sanctions) (EU Exit) Regulations 2019</RegimeName>
<IndividualEntityShip>Entity</IndividualEntityShip><OtherInformation>Names of Director(s)/Management: Somebody|Business Sector: Defence</OtherInformation>
<UKStatementofReasons>Largest manufacturer of small arms.</UKStatementofReasons><Addresses><Address><AddressLine1>Street 3</AddressLine1>
<AddressLine6>Izhevsk</AddressLine6><AddressPostalCode>426006</AddressPostalCode><AddressCountry>Russia</AddressCountry></Address></Addresses>
<EntityDetails><Entity><BusinessRegistrationNumbers><BusinessRegistrationNumber>1111832005440</BusinessRegistrationNumber>
<BusinessRegistrationNumber>1832090230</BusinessRegistrationNumber></BusinessRegistrationNumbers></Entity></EntityDetails></Designation>
<Designation><UniqueID>RUS2</UniqueID><Names><Name><Name6>Somebody Person</Name6><NameType>Primary name</NameType></Name></Names>
<RegimeName>The Russia (Sanctions) (EU Exit) Regulations 2019</RegimeName><IndividualEntityShip>Individual</IndividualEntityShip></Designation></Designations>'''


class Lists(unittest.TestCase):
    def test_ofac_entities_in_russia_only(self):
        got = R.ofac(SDN, ADD)
        self.assertEqual([(r['name'], r['city'], r['inn'], r['defence']) for r in got],
                         [('JSC ACME MISSILE PLANT', 'Tula', '7701234567', True)])  # person and Latvian bank out
        self.assertNotIn('ul. Lenina', str(got))  # no street address

    def test_uk_company_city_numbers_no_people(self):
        got = R.uk(UK)
        self.assertEqual(len(got), 1)
        r = got[0]
        self.assertEqual((r['name'], r['name_ru'], r['city'], r['inn'], r['ogrn'], r['defence']),
                         ('Kalashnikov Concern', 'Концерн Калашников', 'Izhevsk', '1832090230', '1111832005440', True))
        self.assertNotIn('Somebody', str(got))  # directors and individuals never read
        self.assertNotIn('426006', str(got))

    def test_merge_by_inn(self):
        a = dict(R.uk(UK)[0])
        b = dict(a, source='OFAC', source_id='9', name='KALASHNIKOV CONCERN JSC', name_ru=None, url='u')
        m = R.merge([a, b])
        self.assertEqual((len(m), len(m[0]['sources']), m[0]['name_ru']), (1, 2, 'Концерн Калашников'))


class Cities(unittest.TestCase):
    def test_one_spelling(self):
        self.assertEqual({R.city(c) for c in ('St. Petersburg', 'Saint-Petersburg', 'St Petersburg', 'SANKT-PETERBURG')},
                         {'Saint Petersburg'})
        self.assertEqual(R.city('g. Moscow'), 'Moscow')
        self.assertEqual(R.city('IZHEVSK'), 'Izhevsk')


class Model(unittest.TestCase):
    def test_answers_by_index(self):
        recs = [{'name': 'NPO A', 'city': 'Tula'}, {'name': 'BANK B', 'city': 'Moscow'}]
        got = R.ask_industrial(recs, lambda t: '[{"i": 1, "d": "yes"}, {"i": 2, "d": "no"}, {"i": 7, "d": "yes"}]')
        self.assertEqual(got, {0: 'yes', 1: 'no'})
        self.assertEqual(R.ask_industrial(recs, lambda t: 1 / 0), {})


if __name__ == '__main__':
    unittest.main()
