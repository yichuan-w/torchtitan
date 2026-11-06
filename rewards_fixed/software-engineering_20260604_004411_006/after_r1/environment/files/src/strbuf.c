#include "strlib.h"
#include <stdlib.h>
#include <string.h>
strbuf *strbuf_new(void){strbuf*s=malloc(sizeof(strbuf));if(!s)return NULL;s->cap=16;s->data=malloc(s->cap);if(!s->data){free(s);return NULL;}s->data[0]='\0';s->len=0;return s;}
void strbuf_free(strbuf*s){if(s){free(s->data);free(s);}}
void strbuf_append(strbuf*s,const char*t){if(!s||!t)return;size_t l=strlen(t);if(s->len+l+1>s->cap){s->cap=(s->len+l)*2;char*n=realloc(s->data,s->cap);if(!n)return;s->data=n;}memcpy(s->data+s->len,t,l);s->len+=l;s->data[s->len]='\0';}
void strbuf_append_char(strbuf*s,char c){if(!s)return;if(s->len+2>s->cap){s->cap*=2;char*n=realloc(s->data,s->cap);if(!n)return;s->data=n;}s->data[s->len++]=c;s->data[s->len]='\0';}
const char*strbuf_cstr(strbuf*s){if(!s)return NULL;s->data[s->len]='\0';return s->data;}
