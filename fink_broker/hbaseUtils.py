# Copyright 2019-2022 AstroLab Software
# Author: Julien Peloton
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import json

import numpy as np

from pyspark.sql.functions import concat_ws, col
from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.utils import AnalysisException

from fink_broker import __version__ as fbvsn
from fink_science import __version__ as fsvsn

from fink_science.t2.utilities import T2_COLS
from fink_science.xmatch.utils import MANGROVE_COLS

from fink_broker.tester import spark_unit_tests

def load_hbase_data(catalog: str, rowkey: str) -> DataFrame:
    """ Load table data from HBase into a Spark DataFrame

    The row(s) containing the different schemas are skipped (only data is loaded)

    Parameters
    ----------
    catalog: str
        Json string containing the HBase table. See
        `fink_utils.hbase` for more information.
    rowkey: str
        Name of the rowkey

    Returns
    ----------
    df: DataFrame
        Spark DataFrame with the table data
    """
    # Grab the running Spark Session,
    # otherwise create it.
    spark = SparkSession \
        .builder \
        .getOrCreate()

    df = spark.read.option("catalog", catalog)\
        .format("org.apache.hadoop.hbase.spark")\
        .option("hbase.spark.use.hbasecontext", False)\
        .option("hbase.spark.pushdown.columnfilter", True)\
        .load()\
        .filter(~col(rowkey).startswith('schema_'))

    return df

def write_catalog_on_disk(catalog, catalogname) -> None:
    """ Save HBase catalog in json format on disk

    Parameters
    ----------
    catalog: str
        Str-dictionary containing the HBase catalog constructed
        from `construct_hbase_catalog_from_flatten_schema`
    catalogname: str
        Name of the catalog on disk
    """
    with open(catalogname, 'w') as json_file:
        json.dump(catalog, json_file)

def push_to_hbase(df, table_name, rowkeyname, cf, nregion=50, catfolder='.') -> None:
    """ Push DataFrame data to HBase

    Parameters
    ----------
    df: Spark DataFrame
        Spark DataFrame
    table_name: str
        Name of the table in HBase
    rowkeyname: str
        Name of the rowkey in the table
    cf: dict
        Dictionnary containing column names with column family
    nregion: int, optional
        Number of region to create if the table is newly created. Default is 50.
    catfolder: str
        Folder to write catalogs (must exist). Default is current directory.
    """
    # construct the catalog
    hbcatalog_index = construct_hbase_catalog_from_flatten_schema(
        df.schema,
        table_name,
        rowkeyname=rowkeyname,
        cf=cf
    )

    # Push table
    df.write\
        .options(catalog=hbcatalog_index, newtable=nregion)\
        .format("org.apache.hadoop.hbase.spark")\
        .option("hbase.spark.use.hbasecontext", False)\
        .save()

    # write catalog for the table data
    file_name = table_name + '.json'
    write_catalog_on_disk(hbcatalog_index, os.path.join(catfolder, file_name))

    # Construct the schema row - inplace replacement
    schema_row_key_name = 'schema_version'
    df = df.withColumnRenamed(
        rowkeyname,
        schema_row_key_name
    )

    df_index_schema = construct_schema_row(
        df,
        rowkeyname=schema_row_key_name,
        version='schema_{}_{}'.format(fbvsn, fsvsn))

    # construct the hbase catalog for the schema
    hbcatalog_index_schema = construct_hbase_catalog_from_flatten_schema(
        df_index_schema.schema,
        table_name,
        rowkeyname=schema_row_key_name,
        cf=cf)

    # Push the data using the hbase connector
    df_index_schema.write\
        .options(catalog=hbcatalog_index_schema, newtable=nregion)\
        .format("org.apache.hadoop.hbase.spark")\
        .option("hbase.spark.use.hbasecontext", False)\
        .save()

    # write catalog for the schema row
    file_name = table_name + '_schema_row.json'
    write_catalog_on_disk(
        hbcatalog_index_schema, os.path.join(catfolder, file_name)
    )

def load_science_portal_column_names():
    """ Load names of the alert fields to use in the science portal.

    These column names should match DataFrame column names. Careful when
    you update it, as it will change the structure of the HBase table.

    The column names are sorted by column family names:
        - i: for column that identify the alert (original alert)
        - d: for column that further describe the alert (Fink added value)
        - b: for binary blob (FITS image)

    Returns
    --------
    cols_*: list of string
        List of DataFrame column names to use for the science portal

    Examples
    --------
    >>> cols_i, cols_d, cols_b = load_science_portal_column_names()
    >>> print(len(cols_d))
    35
    """
    # Column family i
    cols_i = [
        'objectId',
        'schemavsn',
        'publisher',
        'fink_broker_version',
        'fink_science_version',
        'candidate.*'
    ]

    # Column family d
    cols_d = [
        'cdsxmatch',
        'rf_snia_vs_nonia',
        'snn_snia_vs_nonia',
        'snn_sn_vs_all',
        'mulens',
        'roid',
        'nalerthist',
        'rf_kn_vs_nonkn',
        'tracklet',
        'DR3Name',
        'Plx',
        'e_Plx',
        'gcvs',
        'vsx',
        'x4lac',
        'x3hsp',
        'anomaly_score'
    ]

    # mangrove
    cols_d += [
        col('mangrove.{}'.format(i)).alias('mangrove_{}'.format(i)) for i in MANGROVE_COLS
    ]

    cols_d += [
        col('t2.{}'.format(i)).alias('t2_{}'.format(i)) for i in T2_COLS
    ]

    # cols_d += [
    #     col('lc_features_g.{}'.format(i)).alias('lc_features_g_{}'.format(i)) for i in FEATURES_COLS
    # ]

    # cols_d += [
    #     col('lc_features_r.{}'.format(i)).alias('lc_features_r_{}'.format(i)) for i in FEATURES_COLS
    # ]

    # Column family binary
    cols_b = [
        col('cutoutScience.stampData').alias('cutoutScience_stampData'),
        col('cutoutTemplate.stampData').alias('cutoutTemplate_stampData'),
        col('cutoutDifference.stampData').alias('cutoutDifference_stampData')
    ]

    return cols_i, cols_d, cols_b

def select_relevant_columns(df: DataFrame, cols: list, logger=None, to_create=None) -> DataFrame:
    """ Select columns from `cols` that are actually in `df`.

    It would act as if `df.select(cols, skip_unknown_cols=True)` was possible.

    Parameters
    ----------
    df: DataFrame
        Input Spark DataFrame
    cols: list
        Column names to select
    to_create: list
        Extra columns to create from others, and to include in the `select`.
        Example: df.select(['a', 'b', col('a') + col('c')])

    Returns:
    df: DataFrame

    Examples
    ----------
    >>> import pyspark.sql.functions as F
    >>> df = spark.createDataFrame([{'a': 1, 'b': 2, 'c': 3}])

    >>> select_relevant_columns(df, ['a'])
    DataFrame[a: bigint]

    >>> select_relevant_columns(df, ['a', 'b', 'c'])
    DataFrame[a: bigint, b: bigint, c: bigint]

    >>> select_relevant_columns(df, ['a', 'd'])
    DataFrame[a: bigint]

    >>> select_relevant_columns(df, ['a', 'b'], to_create=[F.col('a') + F.col('b')])
    DataFrame[a: bigint, b: bigint, (a + b): bigint]
    """
    cnames = []
    missing_cols = []
    for col_ in cols:
        # Dumb but simple
        try:
            df.select(col_)
            cnames.append(col_)
        except AnalysisException:
            missing_cols.append(col_)

    if (to_create is not None) and (type(to_create) == list):
        cnames += to_create
    df = df.select(cnames)

    if logger is not None:
        logger.info("Missing columns detected in the DataFrame: {}".format(missing_cols))

    return df

def assign_column_family_names(df, cols_i, cols_d, cols_b):
    """ Assign a column family name to each column qualifier.

    There are currently 3 column families:
        - i: for column that identify the alert (original alert)
        - d: for column that further describe the alert (Fink added value)
        - b: for binary types. It currently contains:
            - binary gzipped FITS image

    The split is done in `load_science_portal_column_names`.

    Parameters
    ----------
    df: DataFrame
        Input DataFrame containing alert data from the raw science DB (parquet).
        See `load_parquet_files` for more information.
    cols_*: list of string
        List of DataFrame column names to use for the science portal.

    Returns
    ---------
    cf: dict
        Dictionary with keys being column names (also called
        column qualifiers), and the corresponding column family.

    """
    cf = {i: 'i' for i in df.select(cols_i).columns}
    cf.update({i: 'd' for i in df.select(cols_d).columns})
    cf.update({i: 'b' for i in df.select(cols_b).columns})

    return cf

def retrieve_row_key_cols():
    """ Retrieve the list of columns to be used to create the row key.

    The column names are defined here. Be careful in not changing it frequently
    as you can replace (remove and add) columns for existing table,
    but you cannot change keys, you must copy the table into new table
    when changing keys design.

    Returns
    --------
    row_key_cols: list of string
    """
    # build the row key: objectId_jd
    row_key_cols = [
        'objectId',
        'jd'
    ]
    return row_key_cols

def attach_rowkey(df, sep='_'):
    """ Create and attach the row key to an existing DataFrame.

    The column used to define the row key are declared in
    `retrieve_row_key_cols`. the row key is made of a string concatenation
    of those column data, with a separator: str(col1_col2_col3_etc)

    Parameters
    ----------
    df: DataFrame
        Input DataFrame containing alert data from the raw science DB (parquet),
        and already flattened with a select (i.e. candidate.jd must be jd).

    Returns
    ----------
    df: DataFrame
        Input DataFrame with a new column with the row key. The type of the
        row key value is string.
    row_key_name: string
        Name of the rowkey, made of the columns that were used.

    Examples
    ----------
    # Read alert from the raw database
    >>> df = spark.read.format("parquet").load(ztf_alert_sample_scidatabase)

    >>> df = df.select(['objectId', 'candidate.*'])

    >>> df_rk, row_key_name = attach_rowkey(df)

    >>> 'objectId_jd' in df_rk.columns
    True
    """
    row_key_cols = retrieve_row_key_cols()
    row_key_name = '_'.join(row_key_cols)

    to_concat = [col(i).astype('string') for i in row_key_cols]

    df = df.withColumn(
        row_key_name,
        concat_ws(sep, *to_concat)
    )
    return df, row_key_name

def construct_hbase_catalog_from_flatten_schema(
        schema: dict, catalogname: str, rowkeyname: str, cf: dict) -> str:
    """ Convert a flatten DataFrame schema into a HBase catalog.

    From
    {'name': 'schemavsn', 'type': 'string', 'nullable': True, 'metadata': {}}

    To
    'schemavsn': {'cf': 'i', 'col': 'schemavsn', 'type': 'string'},

    Parameters
    ----------
    schema : dict
        Schema of the flatten DataFrame.
    catalogname : str
        Name of the HBase catalog.
    rowkeyname : str
        Name of the rowkey in the HBase catalog.
    cf: dict
        Dictionary with keys being column names (also called
        column qualifiers), and the corresponding column family.
        See `assign_column_family_names`.

    Returns
    ----------
    catalog : str
        Catalog for HBase.

    Examples
    --------
    # Read alert from the raw database
    >>> df = spark.read.format("parquet").load(ztf_alert_sample_scidatabase)

    >>> cols_i, cols_d, cols_b = load_science_portal_column_names()

    >>> cf = assign_column_family_names(df, cols_i, [], [])

    # Flatten the DataFrame
    >>> df_flat = df.select(cols_i)

    Attach the row key
    >>> df_rk, row_key_name = attach_rowkey(df_flat)

    >>> catalog = construct_hbase_catalog_from_flatten_schema(
    ...     df_rk.schema, "mycatalogname", row_key_name, cf)
    """
    schema_columns = schema.jsonValue()["fields"]

    catalog = ''.join("""
    {{
        'table': {{
            'namespace': 'default',
            'name': '{}'
        }},
        'rowkey': '{}',
        'columns': {{
    """).format(catalogname, rowkeyname)

    sep = ","
    for column in schema_columns:
        # Last entry should not have comma (malformed json)
        # if schema_columns.index(column) != len(schema_columns) - 1:
        #     sep = ","
        # else:
        #     sep = ""

        # Deal with array
        if type(column["type"]) == dict:
            # column["type"]["type"]
            column["type"] = "string"

        if column["type"] == 'timestamp':
            # column["type"]["type"]
            column["type"] = "string"

        if column["name"] == rowkeyname:
            catalog += """
            '{}': {{'cf': 'rowkey', 'col': '{}', 'type': '{}'}}{}
            """.format(column["name"], column["name"], column["type"], sep)
        else:
            catalog += """
            '{}': {{'cf': '{}', 'col': '{}', 'type': '{}'}}{}
            """.format(
                column["name"],
                cf[column["name"]],
                column["name"],
                column["type"],
                sep
            )

    # Push an empty column family 'a' for later annotations
    catalog += "'annotation': {'cf': 'a', 'col': '', 'type': 'string'}"
    catalog += """
        }
    }
    """

    return catalog.replace("\'", "\"")

def construct_schema_row(df, rowkeyname, version):
    """ Construct a DataFrame whose columns are those of the
    original ones, and one row containing schema types

    Parameters
    ----------
    df: Spark DataFrame
        Input Spark DataFrame. Need to be flattened.
    rowkeyname: string
        Name of the HBase row key (column name)
    version: string
        Version of the HBase table (row value for the rowkey column).

    Returns
    ---------
    df_schema: Spark DataFrame
        Spark DataFrame with one row (the types of its column). Only the row
        key is the version of the HBase table.

    Examples
    --------
    # Read alert from the raw database
    >>> from pyspark.sql.functions import lit
    >>> df = spark.read.format("parquet").load(ztf_alert_sample_scidatabase)

    # inplace replacement
    >>> df = df.select(['objectId', 'candidate.jd', 'candidate.candid', col('cutoutScience.stampData').alias('cutoutScience_stampData')])
    >>> df = df.withColumn('schema_version', lit(''))
    >>> df = construct_schema_row(df, rowkeyname='schema_version', version='schema_v0')
    >>> df.show()
    +--------+------+------+-----------------------+--------------+
    |objectId|    jd|candid|cutoutScience_stampData|schema_version|
    +--------+------+------+-----------------------+--------------+
    |  string|double|  long|             fits/image|     schema_v0|
    +--------+------+------+-----------------------+--------------+
    <BLANKLINE>
    """
    # Grab the running Spark Session,
    # otherwise create it.
    spark = SparkSession \
        .builder \
        .getOrCreate()

    # Original df columns, but values are types.
    data = np.array([(c.jsonValue()['type']) for c in df.schema], dtype='<U75')

    # binary types are too vague, so assign manually a description
    names = np.array([(c.jsonValue()['name']) for c in df.schema])
    mask = np.array(['cutout' in i for i in names])
    data[mask] = 'fits/image'

    index = np.where(np.array(df.columns) == rowkeyname)[0][0]
    data[index] = version

    # Create the DataFrame
    df_schema = spark.createDataFrame([data.tolist()], df.columns)

    return df_schema


if __name__ == "__main__":
    """ Execute the test suite with SparkSession initialised """

    globs = globals()
    root = os.environ['FINK_HOME']
    globs["ztf_alert_sample"] = os.path.join(
        root, "fink-alert-schemas/ztf/template_schema_ZTF_3p3.avro")

    globs["ztf_alert_sample_scidatabase"] = os.path.join(
        root, "online/science")

    # Run the Spark test suite
    spark_unit_tests(globs, withstreaming=False)
