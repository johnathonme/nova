# Copyright 2012 OpenStack Foundation
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from sqlalchemy import String, Column, MetaData, Table, delete, select


def upgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    aggregates = Table('aggregates', meta, autoload=True)
    aggregate_metadata = Table('aggregate_metadata', meta, autoload=True)
    # migrate data
    record_list = list(aggregates.select().execute())
    for rec in record_list:
        row = aggregate_metadata.insert()
        row.execute({'created_at': rec['created_at'],
                    'updated_at': rec['updated_at'],
                    'deleted_at': rec['deleted_at'],
                    'deleted': rec['deleted'],
                    'key': 'availability_zone',
                    'value': rec['availability_zone'],
                    'aggregate_id': rec['id'],
                    })
    aggregates.drop_column('availability_zone')


def downgrade(migrate_engine):
    meta = MetaData()
    meta.bind = migrate_engine

    aggregates = Table('aggregates', meta, autoload=True)
    aggregate_metadata = Table('aggregate_metadata', meta, autoload=True)
    availability_zone = Column('availability_zone', String(255))
    aggregates.create_column(availability_zone)
    # migrate data
    aggregates.update().values(availability_zone=select(
        [aggregate_metadata.c.value]).where(aggregates.c.id ==
        aggregate_metadata.c.aggregate_id).where(aggregate_metadata.c.key ==
        'availability_zone')).execute()
    delete(aggregate_metadata,
           aggregate_metadata.c.key == 'availability_zone').execute()
    aggregates.c.availability_zone.alter(nullable=False)
