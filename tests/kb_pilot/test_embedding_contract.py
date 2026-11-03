"""Exercise the actual SQL projection and decoder with native-shaped tuples.

No vectors/models/SQL services are called. The cursor projects fixture rows from
actual SELECT fields; it does not hide query/decoder mismatches behind a stub.
"""
import contextlib
import copy
from datetime import datetime,timedelta,timezone
import socket
import unittest
from unittest.mock import patch

from collectors.kb_pilot import embed
from tests.kb_pilot.test_body_selection import provider,TEXT

NOW=datetime(2026,11,1,12,tzinfo=timezone.utc)


class NeighbourCursor:
    def __init__(self, rows): self.rows=rows;self.fields=[]
    def execute(self,sql,args):
        self.sql=sql;self.args=args
        self.fields=[s.strip() for s in sql.split('FROM knowledge.item_embedding',1)[0].split('SELECT',1)[1].split(',')]
    def fetchall(self):
        out=[]
        for r in self.rows:
            projected=[]
            for field in self.fields:
                if field.endswith(' AS distance'):projected.append(r['distance'])
                else:projected.append(r[{'e2.item_id':'item_id','e2.text_sha256':'text_sha256',
                    'e2.origin_item_id':'origin_item_id','i2.published_at':'published',
                    'e2.origin_method':'origin_method'}[field]])
            out.append(tuple(projected))
        return out


def neighbour(i=100,hash_='a'*64,distance=0.2,origin=None):
    return {'item_id':i,'text_sha256':hash_,'origin_item_id':origin,'published':NOW-timedelta(days=1),
            'distance':distance,'origin_method':'self'}


class TxCursor(NeighbourCursor):
    def __init__(self,conn):super().__init__([]);self.conn=conn;self.result=[];self.rowcount=0
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def execute(self,sql,args):
        self.conn.calls.append((sql,args))
        if 'LEFT JOIN knowledge.item_embedding' in sql:
            self.kind='pending';self.result=[r for r in self.conn.items if r[0] not in self.conn.records]
        elif sql.lstrip().startswith('INSERT INTO knowledge.item_embedding'):
            self.kind='write';iid,model,hash_,vector=args
            self.rowcount=0 if iid in self.conn.records and 'DO NOTHING' in sql else 1
            if self.rowcount:
                pub=next(r[4] for r in self.conn.items if r[0]==iid)
                self.conn.records[iid]={'item_id':iid,'text_sha256':hash_,'origin_item_id':None,'published':pub,
                                        'distance':0.4,'origin_method':None}
        elif sql.lstrip().startswith('UPDATE knowledge.item_embedding'):
            self.kind='write';origin,method,iid=args;self.conn.records[iid].update(origin_item_id=origin,origin_method=method)
        else:
            self.kind='neighbours';iid=args[0];self.rows=[copy.deepcopy(r) for key,r in self.conn.records.items() if key!=iid]
            super().execute(sql,args)
    def fetchall(self):
        if self.kind=='neighbours':
            if self.conn.bad_shape:return [('SECRET_PRIVATE_VALUE',)*7]
            return super().fetchall()
        return self.result


class Connection:
    def __init__(self,bad_shape=False):
        self.items=[(201,'Example','https://publisher.example/a',{'content_capture':provider(TEXT)},NOW),
                    (202,'Example','https://publisher.example/b',{'content_capture':provider(TEXT)},NOW+timedelta(seconds=1))]
        self.records={100:neighbour()};self.calls=[];self.commits=self.rollbacks=0;self.bad_shape=bad_shape
    def cursor(self):return TxCursor(self)
    def __enter__(self):self.before=copy.deepcopy(self.records);return self
    def __exit__(self,kind,*_):
        if kind:self.records=self.before;self.rollbacks+=1
        return False
    def commit(self):self.commits+=1;self.before=copy.deepcopy(self.records)


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket,'socket',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=AssertionError('NO DNS')))
    def tearDown(self):self.stack.close()

    def test_actual_nonempty_sql_projection_matches_five_field_decoder(self):
        cur=NeighbourCursor([neighbour()]);rows=embed.neighbours(cur,201)
        self.assertEqual(len(cur.fields),5)
        self.assertEqual(rows,[{'item_id':100,'text_sha256':'a'*64,'origin_item_id':None,
                               'published':NOW-timedelta(days=1),'distance':0.2}])
        self.assertNotIn('e2.origin_method',cur.fields)
        self.assertEqual(cur.fields.count('e2.text_sha256'),1)
        self.assertEqual(cur.args,(201,embed.ORIGIN_WINDOW_DAYS,8))

    def test_empty_result_is_self(self):
        rows=embed.neighbours(NeighbourCursor([]),201)
        self.assertEqual(embed.origin_of({'item_id':201,'published':NOW,'text_sha256':'z'*64},rows),(201,'self'))

    def test_same_text_and_near_duplicate_preserve_existing_origin_rules(self):
        item={'item_id':201,'published':NOW,'text_sha256':'a'*64}
        rows=embed.neighbours(NeighbourCursor([neighbour(origin=90)]),201)
        self.assertEqual(embed.origin_of(item,rows),(90,'same_text'))
        rows=embed.neighbours(NeighbourCursor([neighbour(hash_='b'*64,distance=0.01)]),201)
        self.assertEqual(embed.origin_of(item,rows),(100,'near_duplicate'))
        rows=embed.neighbours(NeighbourCursor([neighbour(hash_='b'*64,distance=0.5)]),201)
        self.assertEqual(embed.origin_of(item,rows),(201,'self'))

    def test_unexpected_native_tuple_shape_has_only_a_stable_code(self):
        class WrongCursor(NeighbourCursor):
            def fetchall(self):return self.bad
        for row in (('SECRET_PRIVATE_VALUE',)*7,('SECRET_PRIVATE_VALUE',)*4,None,{'secret':'value'}):
            cur=WrongCursor([]);cur.bad=[row]
            with self.assertRaisesRegex(RuntimeError,'^ORIGIN_ROW_SHAPE_INVALID$') as caught:embed.neighbours(cur,201)
            self.assertNotIn('SECRET',str(caught.exception))

    def test_embedding_write_and_origin_use_actual_nonempty_neighbours(self):
        conn=Connection();calls=[]
        def vectors(texts):calls.append(texts);return [[0.0]*1024 for _ in texts]
        with conn:
            stats=embed.run(conn,'','',backend='local',local=vectors,new_only=True)
        self.assertEqual(stats['embedded'],2);self.assertEqual(conn.commits,1);self.assertEqual(conn.rollbacks,0)
        self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]),2)
        self.assertEqual(conn.records[201]['origin_item_id'],201)
        self.assertEqual(conn.records[201]['origin_method'],'self')
        self.assertEqual(conn.records[202]['origin_item_id'],201)
        self.assertEqual(conn.records[202]['origin_method'],'same_text')
        self.assertEqual(conn.records[100],neighbour())
        self.assertEqual(sum('FROM knowledge.item_embedding e1' in sql for sql,_ in conn.calls),2)

    def test_bad_row_shape_rolls_back_the_whole_uncommitted_batch(self):
        conn=Connection(bad_shape=True);before=copy.deepcopy(conn.records)
        with self.assertRaisesRegex(RuntimeError,'^ORIGIN_ROW_SHAPE_INVALID$'):
            with conn:embed.run(conn,'','',backend='local',local=lambda texts:[[0.0]*1024 for _ in texts],new_only=True)
        self.assertEqual(conn.records,before);self.assertEqual(conn.commits,0);self.assertEqual(conn.rollbacks,1)
        self.assertFalse(any(sql.lstrip().startswith('UPDATE knowledge.item_embedding') for sql,_ in conn.calls))


if __name__=='__main__':unittest.main()
